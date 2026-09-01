"""Structured verdict file (CLI --review-verdict-file) acceptance tests.

Covers experiment 216b6a81 plan v2 acceptance criteria:

* (a) --review-verdict-file YAML contract + Pydantic schema + 8 verdict
      combination cases
* (b) review item id authorization rejection by review_id ownership (3 cases)
* (c) legacy free-text fallback with pre_schema_accept_result='true' warning
* (d) pre_schema_accept_result tag + verdict breakdown in log metadata
* (f) WaivedReason(min_length=50, max_length=1000) boundary cases
* (g) R6 reverse-validation: verdict_breakdown counts == verdicts length
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import yaml
from map_types.enums import ReviewVerdict
from map_types.schemas import (
    ExperimentResultDecision,
    ReviewInvariantCheck,
    ReviewVerdictFile,
    ReviewVerdictItem,
)
from pydantic import ValidationError

from tests._frontmatter import make_valid_plan

# Schema-only tests are unit tests; integration tests below are marked slow
# because they spin up the FastAPI app, db session, and multiple agents.

# ---------------------------------------------------------------------------
# (a) + (f) — Pydantic schema validation: 8 verdict combinations + 4 boundaries
# ---------------------------------------------------------------------------


WAIVER_REASON_50 = "x" * 50
WAIVER_REASON_999 = "x" * 999
WAIVER_REASON_1000 = "x" * 1000
WAIVER_REASON_1001 = "x" * 1001
WAIVER_REASON_49 = "x" * 49


def _valid_item(*, verdict: ReviewVerdict = ReviewVerdict.passed, reason: str | None = None) -> ReviewVerdictItem:
    return ReviewVerdictItem(item_id=uuid.uuid4(), verdict=verdict, reason=reason)


def test_schema_all_pass():
    file = ReviewVerdictFile(
        review_id=uuid.uuid4(),
        verdicts=[_valid_item(), _valid_item(), _valid_item()],
        invariants=[ReviewInvariantCheck(item_id=uuid.uuid4(), verified=True)],
    )
    assert len(file.verdicts) == 3
    assert len(file.invariants) == 1


def test_schema_partial_waive():
    file = ReviewVerdictFile(
        review_id=uuid.uuid4(),
        verdicts=[
            _valid_item(),
            _valid_item(verdict=ReviewVerdict.waived, reason=WAIVER_REASON_50),
        ],
    )
    assert file.verdicts[1].verdict == ReviewVerdict.waived
    assert len(file.verdicts[1].reason or "") == 50


def test_schema_partial_fail():
    file = ReviewVerdictFile(
        review_id=uuid.uuid4(),
        verdicts=[_valid_item(), _valid_item(verdict=ReviewVerdict.failed)],
    )
    assert any(v.verdict == ReviewVerdict.failed for v in file.verdicts)


def test_schema_invariants_optional():
    file = ReviewVerdictFile(review_id=uuid.uuid4(), verdicts=[_valid_item()])
    assert file.invariants == []


def test_schema_empty_verdicts_rejected_by_pydantic_field_default():
    # default_factory=list allows empty list (field is optional at schema layer)
    # but the service-level helper will reject empty verdicts at decision time.
    file = ReviewVerdictFile(review_id=uuid.uuid4())
    assert file.verdicts == []


def test_schema_invalid_verdict_enum():
    with pytest.raises(ValidationError):
        ReviewVerdictItem(item_id=uuid.uuid4(), verdict="bogus")  # type: ignore[arg-type]


def test_schema_waived_without_reason():
    with pytest.raises(ValidationError) as excinfo:
        ReviewVerdictItem(item_id=uuid.uuid4(), verdict=ReviewVerdict.waived, reason=None)
    assert "waived" in str(excinfo.value).lower()


def test_schema_duplicate_item_ids():
    item_id = uuid.uuid4()
    with pytest.raises(ValidationError) as excinfo:
        ReviewVerdictFile(
            review_id=uuid.uuid4(),
            verdicts=[
                ReviewVerdictItem(item_id=item_id, verdict=ReviewVerdict.passed),
                ReviewVerdictItem(item_id=item_id, verdict=ReviewVerdict.failed),
            ],
        )
    assert "Duplicate" in str(excinfo.value)


# (f) — WaivedReason boundary cases


def test_waived_reason_min_length_49_rejected():
    with pytest.raises(ValidationError):
        ReviewVerdictItem(
            item_id=uuid.uuid4(),
            verdict=ReviewVerdict.waived,
            reason=WAIVER_REASON_49,
        )


def test_waived_reason_min_length_50_accepted():
    item = ReviewVerdictItem(
        item_id=uuid.uuid4(),
        verdict=ReviewVerdict.waived,
        reason=WAIVER_REASON_50,
    )
    assert item.reason is not None and len(item.reason) == 50


def test_waived_reason_max_length_1001_rejected():
    with pytest.raises(ValidationError):
        ReviewVerdictItem(
            item_id=uuid.uuid4(),
            verdict=ReviewVerdict.waived,
            reason=WAIVER_REASON_1001,
        )


def test_waived_reason_max_length_999_accepted():
    item = ReviewVerdictItem(
        item_id=uuid.uuid4(),
        verdict=ReviewVerdict.waived,
        reason=WAIVER_REASON_999,
    )
    assert item.reason is not None and len(item.reason) == 999


def test_waived_reason_max_length_1000_accepted():
    item = ReviewVerdictItem(
        item_id=uuid.uuid4(),
        verdict=ReviewVerdict.waived,
        reason=WAIVER_REASON_1000,
    )
    assert item.reason is not None and len(item.reason) == 1000


def test_experiment_result_decision_accepts_optional_verdict_file():
    payload = ExperimentResultDecision(
        summary="ok",
        content_md="done",
        verdict_file=ReviewVerdictFile(
            review_id=uuid.uuid4(),
            verdicts=[_valid_item()],
        ),
    )
    assert payload.verdict_file is not None


def test_experiment_result_decision_default_legacy_no_verdict_file():
    payload = ExperimentResultDecision(summary="ok", content_md="done")
    assert payload.verdict_file is None


# ---------------------------------------------------------------------------
# Integration fixtures: spin up an experiment in result_review with reviews
# ---------------------------------------------------------------------------


@pytest.fixture
def result_review_experiment(client, auth_headers, reviewer, project) -> dict:
    """Same shape as approved_experiment in test_m3_flow but advanced to
    result_review so accept-result can be exercised with a verdict file.
    """
    experiment = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "verdict 测试实验",
            "plan": {"content_md": make_valid_plan(body="## plan")},
            "submit_for_review": True,
        },
    ).json()
    exp_id = experiment["id"]

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={
            "reasonable_items": ["OK"],
            "unreasonable_items": ["边界待补"],
        },
    ).json()
    # Mark the unreasonable item as withdrawn (reviewer is allowed open→withdrawn)
    # so approval can proceed. We keep the item record around for the verdict
    # tests that follow.
    for item in review["items"]:
        if item["kind"] == "unreasonable":
            patch_resp = client.patch(
                f"/api/v1/review-items/{item['id']}",
                headers=reviewer["headers"],
                json={"status": "withdrawn"},
            )
            if patch_resp.status_code != 200:
                raise AssertionError(
                    f"withdraw failed: {patch_resp.status_code} {patch_resp.text}"
                )

    approve_resp = client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    if approve_resp.status_code != 200:
        raise AssertionError(f"approve failed: {approve_resp.status_code} {approve_resp.text}")
    start_resp = client.post(f"/api/v1/experiments/{exp_id}/start", headers=auth_headers)
    if start_resp.status_code != 200:
        raise AssertionError(f"start failed: {start_resp.status_code} {start_resp.text}")
    completed = client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=auth_headers,
        json={
            "summary": "实验完成",
            "content_md": "## 结果",
            "metadata": {"pytest_summary": "unit passed"},
        },
    )
    if completed.status_code != 200:
        raise AssertionError(f"complete failed: {completed.status_code} {completed.text}")
    if completed.json()["phase"] != "result_review":
        raise AssertionError(f"phase after complete: {completed.json()['phase']}")
    return {
        "project_id": project["id"],
        "experiment_id": exp_id,
        "review_id": review["id"],
        "items": review["items"],
    }


# ---------------------------------------------------------------------------
# (c) — Legacy free-text fallback path: pre_schema_accept_result='true'
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_legacy_accept_result_records_pre_schema_true(result_review_experiment, client, reviewer):
    exp_id = result_review_experiment["experiment_id"]
    response = client.post(
        f"/api/v1/experiments/{exp_id}/accept-result",
        headers=reviewer["headers"],
        json={"summary": "legacy approval", "content_md": "just summary"},
    )
    assert response.status_code == 200
    assert response.json()["phase"] == "done"

    logs = client.get(f"/api/v1/experiments/{exp_id}/logs", headers=reviewer["headers"]).json()
    approval_log = next(log for log in logs if log["summary"] == "legacy approval")
    assert approval_log["metadata_json"]["pre_schema_accept_result"] == "true"
    assert approval_log["metadata_json"]["verdict_breakdown"] is None


# ---------------------------------------------------------------------------
# (a) + (d) — Structured verdict file: pre_schema_accept_result='false'
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_structured_verdict_records_breakdown_and_tag(result_review_experiment, client, reviewer):
    exp_id = result_review_experiment["experiment_id"]
    review_id = result_review_experiment["review_id"]
    items = result_review_experiment["items"]
    waived_item_id = next(i["id"] for i in items if i["kind"] == "unreasonable")

    payload = {
        "summary": "通过 + 1 豁免",
        "content_md": "## result",
        "verdict_file": {
            "review_id": review_id,
            "verdicts": [
                {"item_id": waived_item_id, "verdict": "waived", "reason": WAIVER_REASON_50},
            ],
        },
    }
    response = client.post(
        f"/api/v1/experiments/{exp_id}/accept-result",
        headers=reviewer["headers"],
        json=payload,
    )
    assert response.status_code == 200, response.text

    logs = client.get(f"/api/v1/experiments/{exp_id}/logs", headers=reviewer["headers"]).json()
    approval_log = next(log for log in logs if log["summary"] == "通过 + 1 豁免")
    meta = approval_log["metadata_json"]
    assert meta["pre_schema_accept_result"] == "false"
    assert meta["verdict_breakdown"] == {"passed": 0, "failed": 0, "waived": 1}
    assert meta["verdict_file"]["review_id"] == review_id


@pytest.mark.slow
def test_structured_verdict_breakdown_counts_match_verdicts(result_review_experiment, client, reviewer):
    """(g) R6 reverse-validation: counts == verdict length."""
    exp_id = result_review_experiment["experiment_id"]
    review_id = result_review_experiment["review_id"]
    items = result_review_experiment["items"]
    waived = next(i["id"] for i in items if i["kind"] == "unreasonable")
    extra = uuid.uuid4()  # an item_id not in any review → must be rejected

    payload = {
        "summary": "计数校验",
        "content_md": "## result",
        "verdict_file": {
            "review_id": str(review_id),
            "verdicts": [
                {"item_id": str(waived), "verdict": "waived", "reason": WAIVER_REASON_50},
                {"item_id": str(extra), "verdict": "passed"},
            ],
        },
    }
    response = client.post(
        f"/api/v1/experiments/{exp_id}/accept-result",
        headers=reviewer["headers"],
        json=payload,
    )
    assert response.status_code == 403, response.text


# ---------------------------------------------------------------------------
# (b) — Authorization rejection by review_id ownership (3 cases)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_authorization_legal_path(result_review_experiment, client, reviewer):
    """合法路径: 当前 review 的 item_id → 200 OK."""
    exp_id = result_review_experiment["experiment_id"]
    review_id = result_review_experiment["review_id"]
    items = result_review_experiment["items"]
    waived = next(i["id"] for i in items if i["kind"] == "unreasonable")

    response = client.post(
        f"/api/v1/experiments/{exp_id}/accept-result",
        headers=reviewer["headers"],
        json={
            "summary": "合法 verdict",
            "content_md": "## result",
            "verdict_file": {
                "review_id": review_id,
                "verdicts": [{"item_id": waived, "verdict": "waived", "reason": WAIVER_REASON_50}],
            },
        },
    )
    assert response.status_code == 200


@pytest.mark.slow
def test_authorization_same_reviewer_cross_experiment_rejected(client, auth_headers, reviewer, project):
    """同 reviewer 跨 experiment item_id 串台: 用历史已关闭实验的 item_id → 403."""
    # Build experiment A (in result_review)
    exp_a = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "exp A", "plan": {"content_md": make_valid_plan(body="## p")}, "submit_for_review": True},
    ).json()
    review_a = client.post(
        f"/api/v1/experiments/{exp_a['id']}/reviews",
        headers=reviewer["headers"],
        json={"unreasonable_items": ["A 待解决"]},
    ).json()
    item_a = next(i for i in review_a["items"] if i["kind"] == "unreasonable")
    client.patch(
        f"/api/v1/review-items/{item_a['id']}",
        headers=reviewer["headers"],
        json={"status": "withdrawn"},
    )
    client.post(f"/api/v1/experiments/{exp_a['id']}/approve", headers=auth_headers)
    client.post(f"/api/v1/experiments/{exp_a['id']}/start", headers=auth_headers)
    client.post(
        f"/api/v1/experiments/{exp_a['id']}/complete",
        headers=auth_headers,
        json={"summary": "A 完成", "content_md": "## r", "metadata": {"pytest_summary": "ok"}},
    )
    # close experiment A
    client.post(
        f"/api/v1/experiments/{exp_a['id']}/accept-result",
        headers=reviewer["headers"],
        json={"summary": "A 通过", "content_md": "## done"},
    )

    # Build experiment B (in result_review) with a different review_id
    exp_b = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "exp B", "plan": {"content_md": make_valid_plan(body="## p")}, "submit_for_review": True},
    ).json()
    review_b = client.post(
        f"/api/v1/experiments/{exp_b['id']}/reviews",
        headers=reviewer["headers"],
        json={"unreasonable_items": ["B 待解决"]},
    ).json()
    item_b = next(i for i in review_b["items"] if i["kind"] == "unreasonable")
    client.patch(
        f"/api/v1/review-items/{item_b['id']}",
        headers=reviewer["headers"],
        json={"status": "withdrawn"},
    )
    client.post(f"/api/v1/experiments/{exp_b['id']}/approve", headers=auth_headers)
    client.post(f"/api/v1/experiments/{exp_b['id']}/start", headers=auth_headers)
    client.post(
        f"/api/v1/experiments/{exp_b['id']}/complete",
        headers=auth_headers,
        json={"summary": "B 完成", "content_md": "## r", "metadata": {"pytest_summary": "ok"}},
    )

    # Try to use item_a (from closed experiment A) when accepting B → 403
    response = client.post(
        f"/api/v1/experiments/{exp_b['id']}/accept-result",
        headers=reviewer["headers"],
        json={
            "summary": "跨 experiment 串台",
            "content_md": "## result",
            "verdict_file": {
                "review_id": review_b["id"],
                "verdicts": [
                    {"item_id": item_a["id"], "verdict": "waived", "reason": WAIVER_REASON_50}
                ],
            },
        },
    )
    assert response.status_code == 403
    assert "does not belong" in response.json()["detail"]


@pytest.mark.slow
def test_authorization_different_reviewer_same_experiment_rejected(client, auth_headers, admin_headers, project):
    """不同 reviewer 同 experiment 引用: 用另一 reviewer 的 item_id → 403.

    The service must reject by item_id ownership: an item_id authored under a
    different review must not be accepted even when verdict_file.review_id
    points at *some* review on the current experiment.
    """
    # Creator's review (admin acting as substitute reviewer of creator's plan)
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "exp cross-review", "plan": {"content_md": make_valid_plan(body="## p")}, "submit_for_review": True},
    ).json()

    # Review #1: admin (substitute, requires substitute_reason)
    review_admin = client.post(
        f"/api/v1/experiments/{exp['id']}/reviews",
        headers=admin_headers,
        json={
            "unreasonable_items": ["admin 待解决"],
            "substitute_reason": "代审原因：admin 顶替 reviewer 提交评审",
        },
    )
    if review_admin.status_code != 201:
        raise AssertionError(
            f"admin review #1 failed: {review_admin.status_code} {review_admin.text}"
        )
    review_admin = review_admin.json()
    item_admin = next(i for i in review_admin["items"] if i["kind"] == "unreasonable")

    # Withdraw so approval can proceed (reviewer open→withdrawn path)
    client.patch(
        f"/api/v1/review-items/{item_admin['id']}",
        headers=admin_headers,
        json={"status": "withdrawn"},
    )
    client.post(f"/api/v1/experiments/{exp['id']}/approve", headers=auth_headers)
    client.post(f"/api/v1/experiments/{exp['id']}/start", headers=auth_headers)
    client.post(
        f"/api/v1/experiments/{exp['id']}/complete",
        headers=auth_headers,
        json={"summary": "完成", "content_md": "## r", "metadata": {"pytest_summary": "ok"}},
    )

    # Now: try a verdict file whose review_id is a fabricated UUID that doesn't
    # belong to this experiment, with item_id from this experiment's review.
    bogus_review_id = str(uuid.uuid4())
    response = client.post(
        f"/api/v1/experiments/{exp['id']}/accept-result",
        headers=admin_headers,
        json={
            "summary": "不同 reviewer 同 experiment 串台",
            "content_md": "## result",
            "verdict_file": {
                "review_id": bogus_review_id,
                "verdicts": [
                    {"item_id": item_admin["id"], "verdict": "waived", "reason": WAIVER_REASON_50}
                ],
            },
        },
    )
    assert response.status_code == 403
    assert "does not belong" in response.json()["detail"]


@pytest.mark.slow
def test_authorization_item_id_from_different_review_rejected(
    client, auth_headers, admin_headers, project
):
    """item_id 引用另一 review 的 item（review_id 合法但 item 越权）→ 403."""
    # Create experiment
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "exp item-跨 review", "plan": {"content_md": make_valid_plan(body="## p")}, "submit_for_review": True},
    ).json()
    # Review #1 by admin (substitute, requires substitute_reason)
    review_1_resp = client.post(
        f"/api/v1/experiments/{exp['id']}/reviews",
        headers=admin_headers,
        json={
            "unreasonable_items": ["review1 项"],
            "substitute_reason": "代审：跨 reviewer 越权测试准备",
        },
    )
    if review_1_resp.status_code != 201:
        raise AssertionError(
            f"admin review #1 failed: {review_1_resp.status_code} {review_1_resp.text}"
        )
    review_1 = review_1_resp.json()
    item_1 = next(i for i in review_1["items"] if i["kind"] == "unreasonable")
    client.patch(
        f"/api/v1/review-items/{item_1['id']}",
        headers=admin_headers,
        json={"status": "withdrawn"},
    )
    # Now add a 2nd review via an external reviewer-agent
    reviewer_agent = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={"name": "reviewer-2nd", "role": "agent", "project_key": project["project_key"]},
    ).json()
    rev2_headers = {"Authorization": f"Bearer {reviewer_agent['api_token']}"}
    review_2 = client.post(
        f"/api/v1/experiments/{exp['id']}/reviews",
        headers=rev2_headers,
        json={"unreasonable_items": ["review2 项"]},
    ).json()
    item_2 = next(i for i in review_2["items"] if i["kind"] == "unreasonable")

    # Resolve review_2's item so plan can advance (review_1 already withdrawn)
    client.patch(
        f"/api/v1/review-items/{item_2['id']}",
        headers=rev2_headers,
        json={"status": "withdrawn"},
    )
    client.post(f"/api/v1/experiments/{exp['id']}/approve", headers=auth_headers)
    client.post(f"/api/v1/experiments/{exp['id']}/start", headers=auth_headers)
    client.post(
        f"/api/v1/experiments/{exp['id']}/complete",
        headers=auth_headers,
        json={"summary": "完成", "content_md": "## r", "metadata": {"pytest_summary": "ok"}},
    )

    # accept-result with review_id = review_2 but item_id from review_1 → 403
    response = client.post(
        f"/api/v1/experiments/{exp['id']}/accept-result",
        headers=admin_headers,
        json={
            "summary": "item 越权",
            "content_md": "## result",
            "verdict_file": {
                "review_id": review_2["id"],
                "verdicts": [
                    {"item_id": item_1["id"], "verdict": "waived", "reason": WAIVER_REASON_50}
                ],
            },
        },
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# CLI helper: _load_review_verdict_file
# ---------------------------------------------------------------------------


def test_cli_load_review_verdict_file_happy_path(tmp_path: Path):
    from cli.commands.experiment_lifecycle import _load_review_verdict_file

    review_id = str(uuid.uuid4())
    item_id = str(uuid.uuid4())
    yaml_path = tmp_path / "verdict.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "review_id": review_id,
                "verdicts": [{"item_id": item_id, "verdict": "waived", "reason": WAIVER_REASON_50}],
            }
        )
    )
    loaded = _load_review_verdict_file(yaml_path)
    assert loaded is not None
    assert str(loaded.review_id) == review_id
    assert loaded.verdicts[0].verdict == ReviewVerdict.waived


def test_cli_load_review_verdict_file_returns_none_when_omitted():
    from cli.commands.experiment_lifecycle import _load_review_verdict_file

    assert _load_review_verdict_file(None) is None


# ---------------------------------------------------------------------------
# CLI → SDK end-to-end: regression for review 216b6a81 reject
# ---------------------------------------------------------------------------
# Reviewer rejected the experiment because `sdk/python/map_client/client.py`
# used `payload.model_dump()` (no `mode="json"`) when posting accept/reject,
# which made the structured verdict path blow up with
# `TypeError: Object of type UUID is not JSON serializable` before reaching
# the server. The two tests below drive the CLI through `CliRunner` +
# `MAPTestClientTransport` to lock in the end-to-end path:
#   (1) happy path: CLI builds payload → SDK serializes → server records
#       `pre_schema_accept_result='false'` + verdict_breakdown.
#   (2) schema error: invalid YAML → CLI exits 2 + 友好提示 + 不漏 traceback.


@pytest.fixture
def patched_reviewer_cli(monkeypatch, client, reviewer):
    from map_client import project_config
    from map_client.testing import MAPTestClientTransport

    import cli.main as cli_main

    token = reviewer["headers"]["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.delenv("MAP_PROJECT_KEY", raising=False)
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))
    monkeypatch.setattr(cli_main, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(project_config, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "map_client.config.load_config",
        lambda *args, **kwargs: {"api_url": "http://test", "token": token, "project_key": None},
    )


@pytest.mark.slow
def test_cli_accept_result_with_verdict_file_happy_path(
    result_review_experiment,
    client,
    reviewer,
    patched_reviewer_cli,
    tmp_path: Path,
):
    """CLI `experiment accept-result --review-verdict-file <yaml>` runs end-to-end:
    SDK serializes verdict_file.review_id / verdicts[].item_id as JSON strings,
    server records pre_schema_accept_result='false' + verdict_breakdown.
    """
    from typer.testing import CliRunner

    from cli.main import app

    runner = CliRunner()
    exp_id = result_review_experiment["experiment_id"]
    review_id = result_review_experiment["review_id"]
    items = result_review_experiment["items"]
    waived_item_id = next(i["id"] for i in items if i["kind"] == "unreasonable")

    verdict_yaml = tmp_path / "verdict.yaml"
    verdict_yaml.write_text(
        yaml.safe_dump(
            {
                "review_id": review_id,
                "verdicts": [
                    {
                        "item_id": waived_item_id,
                        "verdict": "waived",
                        "reason": WAIVER_REASON_50,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    log_md = tmp_path / "log.md"
    log_md.write_text("## cli end-to-end accept-result\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "experiment",
            "accept-result",
            "--id",
            exp_id,
            "--summary",
            "CLI 端到端通过 + 1 豁免",
            "--file",
            str(log_md),
            "--review-verdict-file",
            str(verdict_yaml),
        ],
    )
    assert result.exit_code == 0, result.output

    logs = client.get(f"/api/v1/experiments/{exp_id}/logs", headers=reviewer["headers"]).json()
    approval_log = next(log for log in logs if log["summary"] == "CLI 端到端通过 + 1 豁免")
    meta = approval_log["metadata_json"]
    assert meta["pre_schema_accept_result"] == "false"
    assert meta["verdict_breakdown"] == {"passed": 0, "failed": 0, "waived": 1}
    assert meta["verdict_file"]["review_id"] == review_id
    assert meta["verdict_file"]["verdicts"][0]["item_id"] == waived_item_id


@pytest.mark.slow
def test_cli_accept_result_with_verdict_file_schema_error(
    patched_reviewer_cli,
    tmp_path: Path,
):
    """Invalid verdict YAML must fail CLI-side before HTTP: exit 2 + 友好提示
    + 不漏 traceback. This protects the structured path from leaking
    ValidationError tracebacks to the user.
    """
    from typer.testing import CliRunner

    from cli.main import app

    runner = CliRunner()
    bad_yaml = tmp_path / "verdict.yaml"
    bad_yaml.write_text(
        yaml.safe_dump(
            {
                "review_id": str(uuid.uuid4()),
                "verdicts": [
                    {"item_id": str(uuid.uuid4()), "verdict": "bogus", "reason": "x"},
                ],
            }
        ),
        encoding="utf-8",
    )
    log_md = tmp_path / "log.md"
    log_md.write_text("## should not reach server\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "experiment",
            "accept-result",
            "--id",
            str(uuid.uuid4()),
            "--summary",
            "should not reach server",
            "--file",
            str(log_md),
            "--review-verdict-file",
            str(bad_yaml),
        ],
    )
    assert result.exit_code == 2, result.output
    assert "invalid --review-verdict-file" in result.output
    assert "Traceback" not in result.output


def test_sdk_accept_experiment_result_serializes_uuids_as_strings():
    """Regression for review 216b6a81 reject root cause: SDK must use
    `model_dump(mode="json")` so review_id / item_id become JSON strings.
    """
    import json

    from map_types.schemas import (
        ExperimentResultDecision,
        ReviewVerdictFile,
        ReviewVerdictItem,
    )

    payload = ExperimentResultDecision(
        summary="unit",
        content_md="x",
        verdict_file=ReviewVerdictFile(
            review_id=uuid.uuid4(),
            verdicts=[
                ReviewVerdictItem(
                    item_id=uuid.uuid4(),
                    verdict=ReviewVerdict.passed,
                ),
            ],
        ),
    )
    serialized = payload.model_dump(mode="json")
    json.dumps(serialized)  # would raise TypeError on UUID without mode="json"
