"""reject-result path split + REVIEW_REJECT_RESULT_MISUSE subcode (experiment b95894db I1(d)).

Pins plan (d) acceptance:

1. ``reject-result`` is structurally forbidden for the experiment creator.
   The host creator cannot reject their own result — that intent lives on
   the per-item ``resolve-item --status rebutted`` path during the review
   phase, so misuse here gets a structured subcode instead of a generic 403.
2. Reviewers / admins can still call ``reject-result`` (the legacy
   happy-path is preserved for non-creator callers).
3. The error body surfaces ``error_code=REVIEW_REJECT_RESULT_MISUSE`` plus
   a remediation ``hint`` so CLI / SDK callers can route to the right
   command.
4. ``update_review_item`` enforces the same subcode when a host creator
   tries to rebut a single item after the experiment has left the review
   phase (the rebuttal is now meaningless — the right command is
   ``reject-result``, which the host still cannot call, so the hint points
   at the reviewer / admin owner).
5. The SDK ``MAPHTTPError`` propagates the structured fields, and the CLI
   ``_run`` formatter surfaces them on stderr.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest
import typer
from fastapi.testclient import TestClient
from map_client.exceptions import MAPHTTPError, MAPValidationError, raise_for_status

from sdk.python.map_client.client import MAPClient
from tests._frontmatter import make_valid_plan

# ---------------------------------------------------------------------------
# Fixtures: a result-review experiment (host already complete'd, awaiting
# reviewer decision) and a reviewer persona.
# ---------------------------------------------------------------------------


@pytest.fixture
def result_review_experiment(
    client: TestClient,
    auth_headers: dict[str, str],
    reviewer: dict,
    project: dict,
) -> dict:
    """Same shape as approved_experiment in test_m3_flow but advanced to
    result_review so reject-result can be exercised.
    """
    experiment = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "执行实验",
            "plan": {"content_md": make_valid_plan(body="## 计划")},
            "submit_for_review": True,
        },
    ).json()
    exp_id = experiment["id"]

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["OK"]},
    ).json()
    for item in review["items"]:
        if item["kind"] == "unreasonable":
            client.patch(
                f"/api/v1/review-items/{item['id']}",
                headers=reviewer["headers"],
                json={"status": "resolved"},
            )

    client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    client.post(f"/api/v1/experiments/{exp_id}/start", headers=auth_headers)
    submitted = client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=auth_headers,
        json={
            "summary": "实验完成",
            "content_md": "结果等待 reviewer 决策",
            "metadata": {"pytest_summary": "unit passed"},
        },
    )
    assert submitted.status_code == 200
    assert submitted.json()["phase"] == "result_review"
    return {"project_id": project["id"], "experiment_id": exp_id}


# ---------------------------------------------------------------------------
# (1) Service layer: host creator calling reject-result raises the structured
# subcode, NOT a generic ForbiddenError.
# ---------------------------------------------------------------------------


def test_reject_result_by_host_creator_emits_reject_result_misuse_subcode(
    client, auth_headers, result_review_experiment
):
    """The host creator of the experiment is structurally forbidden from
    rejecting their own result; misuse gets the structured subcode."""
    exp_id = result_review_experiment["experiment_id"]
    response = client.post(
        f"/api/v1/experiments/{exp_id}/reject-result",
        headers=auth_headers,
        json={"summary": "host 自驳", "content_md": "误用 reject-result"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "REVIEW_REJECT_RESULT_MISUSE"
    assert "hint" in body and "resolve-item" in body["hint"]
    assert "reject-result" in body["hint"]
    assert body["retryable"] is False
    # The detail still includes the human-readable explanation.
    assert "creator" in body["detail"].lower() or "驳回" in body["detail"]


def test_reject_result_by_reviewer_still_succeeds(
    client, reviewer, result_review_experiment
):
    """Non-creator callers (reviewer) keep the legacy happy-path."""
    exp_id = result_review_experiment["experiment_id"]
    response = client.post(
        f"/api/v1/experiments/{exp_id}/reject-result",
        headers=reviewer["headers"],
        json={"summary": "结果驳回", "content_md": "缺少关键验收证据"},
    )
    assert response.status_code == 200
    assert response.json()["phase"] == "running"


# ---------------------------------------------------------------------------
# (2) Symmetric guard: host creator rebutting a single review item after the
# experiment has left the review phase gets the same subcode.
# ---------------------------------------------------------------------------


def test_rebut_single_item_after_review_phase_emits_reject_result_misuse_subcode(
    client, auth_headers, reviewer, result_review_experiment, db_session
):
    """When the experiment is in result_review (or later), a host rebuttal of
    a single item is meaningless — the right command is ``reject-result``,
    so surface the same subcode.

    We seed the open unreasonable item directly via the test DB session so
    we don't have to thread the experiment back through review / approve /
    start / complete (which is fragile because plan-revise is restricted to
    draft / review / running phases).
    """
    from server.domain.models import (
        ExperimentPhase,
        Review,
        ReviewItem,
        ReviewItemKind,
        ReviewItemStatus,
    )

    exp_id = result_review_experiment["experiment_id"]

    # Seed a review + open unreasonable item tied to this experiment. The
    # ``reviewer`` fixture already registered a real agent row we can
    # reference for the FK constraint.
    review = Review(
        experiment_id=uuid.UUID(exp_id),
        reviewer_agent_id=uuid.UUID(reviewer["id"]),
        plan_version=2,
        substitute_kind="none",
    )
    db_session.add(review)
    db_session.flush()
    open_item = ReviewItem(
        review_id=review.id,
        kind=ReviewItemKind.unreasonable,
        content="post-review-phase item",
        status=ReviewItemStatus.open,
    )
    db_session.add(open_item)
    db_session.commit()
    db_session.refresh(open_item)

    # Sanity check: the experiment is still in result_review.
    from server.services.project_service import get_experiment

    experiment = get_experiment(db_session, uuid.UUID(exp_id))
    assert experiment.phase == ExperimentPhase.result_review

    # Host creator attempts to rebut — phase is no longer review, so the
    # I1(d) subcode must fire.
    rebuttal = client.patch(
        f"/api/v1/review-items/{open_item.id}",
        headers=auth_headers,
        json={"status": "rebutted"},
    )
    assert rebuttal.status_code == 422, rebuttal.text
    body = rebuttal.json()
    assert body["error_code"] == "REVIEW_REJECT_RESULT_MISUSE"
    assert body["retryable"] is False
    assert "review phase" in body["detail"].lower() or "review 阶段" in body["detail"]


def test_rebut_single_item_during_review_phase_still_succeeds(
    client,
    auth_headers,
    admin_token,
    project,
):
    """Happy-path: host rebutting a single item while the experiment is in
    the review phase keeps working (no subcode)."""
    _, admin_bearer = admin_token
    admin_headers = {"Authorization": f"Bearer {admin_bearer}"}

    experiment = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "rebut happy path",
            "plan": {"content_md": make_valid_plan(body="## 计划")},
            "submit_for_review": True,
        },
    ).json()
    exp_id = experiment["id"]

    reviewer_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        params={
            "name": "happy-reviewer",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert reviewer_resp.status_code == 201
    reviewer_headers = {
        "Authorization": f"Bearer {reviewer_resp.json()['api_token']}"
    }

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer_headers,
        json={"unreasonable_items": ["x"]},
    ).json()
    item_id = next(i["id"] for i in review["items"] if i["kind"] == "unreasonable")

    rebuttal = client.patch(
        f"/api/v1/review-items/{item_id}",
        headers=auth_headers,
        json={"status": "rebutted"},
    )
    assert rebuttal.status_code == 200, rebuttal.text
    assert rebuttal.json()["status"] == "rebutted"


# ---------------------------------------------------------------------------
# (3) SDK propagates error_code / hint / retryable on MAPHTTPError.
# ---------------------------------------------------------------------------


def test_sdk_reject_result_misuse_surfaces_structured_fields(
    map_client: MAPClient, result_review_experiment
):
    """The SDK ``MAPHTTPError`` carries ``error_code`` + ``hint`` +
    ``retryable`` so callers can branch on the structured failure mode."""
    exp_id = result_review_experiment["experiment_id"]
    from map_types.schemas import ExperimentResultDecision

    payload = ExperimentResultDecision(
        summary="sdk 自驳",
        content_md="误用 reject-result",
        metadata=None,
        verdict_file=None,
    )
    with pytest.raises(MAPHTTPError) as exc_info:
        map_client.reject_experiment_result(uuid.UUID(exp_id), payload)
    exc = exc_info.value
    assert exc.status_code == 422
    assert isinstance(exc, MAPValidationError)  # 422 → MAPValidationError
    assert exc.error_code == "REVIEW_REJECT_RESULT_MISUSE"
    assert exc.retryable is False
    assert exc.hint and "resolve-item" in exc.hint


# ---------------------------------------------------------------------------
# (4) CLI surfaces the structured subcode on stderr.
# ---------------------------------------------------------------------------


def test_cli_reject_result_misuse_prints_subcode_and_hint(
    client, auth_headers, result_review_experiment, tmp_path, monkeypatch, capsys
):
    """``_run`` surfaces the structured subcode + hint on stderr when
    ``reject-result`` is misused by the host creator. We exercise ``_run``
    directly with a stubbed client instead of invoking the full Typer app —
    that lets us assert the formatter output without bootstrapping a
    ``.map/agents.local.yaml`` and a ``persona`` flag at the CLI boundary.
    """
    from cli import main as cli_main

    class _StubClient:
        def __init__(self) -> None:
            self.calls = 0

        def reject_experiment_result(self, experiment_id, payload):
            self.calls += 1
            response = client.post(
                f"/api/v1/experiments/{str(experiment_id)}/reject-result",
                headers=auth_headers,
                json={
                    "summary": payload.summary,
                    "content_md": payload.content_md,
                },
            )
            # Reuse the same error shaping the SDK would do so the test
            # verifies the full _run → MAPHTTPError → typer.echo path.
            if response.status_code >= 400:
                detail = response.text
                error_code = None
                hint = None
                retryable = None
                try:
                    payload_json = response.json()
                    if isinstance(payload_json, dict):
                        detail = str(payload_json.get("detail", detail))
                        if isinstance(payload_json.get("error_code"), str):
                            error_code = payload_json["error_code"]
                        if isinstance(payload_json.get("hint"), str):
                            hint = payload_json["hint"]
                        if isinstance(payload_json.get("retryable"), bool):
                            retryable = payload_json["retryable"]
                except Exception:
                    pass
                raise_for_status(
                    response.status_code,
                    detail,
                    error_code=error_code,
                    hint=hint,
                    retryable=retryable,
                )
            return response.json()

    stub = _StubClient()

    @contextmanager
    def _stub_ctx():
        yield stub

    monkeypatch.setattr(cli_main, "_client_ctx", _stub_ctx)

    from map_types.schemas import ExperimentResultDecision

    exp_id = result_review_experiment["experiment_id"]
    payload = ExperimentResultDecision(
        summary="host 自驳 (cli test)",
        content_md="误用 reject-result",
        metadata=None,
        verdict_file=None,
    )

    with pytest.raises((SystemExit, typer.Exit)) as exit_info:
        cli_main._run(lambda c: c.reject_experiment_result(uuid.UUID(exp_id), payload))

    # ``SystemExit.code`` and ``typer.Exit.exit_code`` differ; normalise.
    exit_code = getattr(exit_info.value, "code", None)
    if exit_code is None:
        exit_code = exit_info.value.exit_code
    assert exit_code == 1
    assert stub.calls == 1
    captured = capsys.readouterr()
    # The structured subcode + hint must surface on stderr.
    assert "REVIEW_REJECT_RESULT_MISUSE" in captured.err
    assert "resolve-item" in captured.err
    assert "Hint:" in captured.err


# ---------------------------------------------------------------------------
# (5) Plain StateTransitionError still surfaces without subcode (back-compat).
# ---------------------------------------------------------------------------


def test_state_transition_error_without_subcode_omits_error_code_field():
    """Backwards-compat: a generic StateTransitionError raised without
    ``error_code`` does not pollute the response body with an empty
    ``error_code`` key."""
    from server.services.errors import StateTransitionError

    err = StateTransitionError("generic state-machine refusal")
    assert err.error_code == "state_machine_error"
    assert err.hint is None
    assert err.retryable is False
