"""50cddb7e I4 (A3+A4) — complete 时 pytest_summary 机器校验门禁 E2E。

Pins the plan acceptance:

* ``metadata["pytest_summary"]["failed"] > 0`` → complete 被拒（409
  StateTransitionError，actionable 提示），phase 保持 running。
* 带 ``known_failures``（CLI ``--known-failures`` 透传到 payload）→ 豁免放行，
  completion log 记录 exempted 状态（reviewer 可见，A4 complete 路径）。
* ``pytest_summary.total`` 与 CI 基线（``MAP_CI_TEST_TOTAL``）不符 → 仅 warning
  （不阻断）。
* accept-result 路径复校 pytest_summary（A4 accept 路径）——最近一条携带
  pytest_summary 的 log 的 evidence 被重跑并追加进 accept log。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests._frontmatter import make_valid_plan

_GREEN = {"total": 35, "passed": 35, "failed": 0}
_RED = {"total": 35, "passed": 30, "failed": 5}


@pytest.fixture
def running_experiment(
    client: TestClient,
    auth_headers: dict[str, str],
    reviewer: dict,
    project: dict,
) -> dict:
    """An experiment in the ``running`` phase, ready for ``complete``."""
    experiment = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "pytest_summary gate",
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
    started = client.post(f"/api/v1/experiments/{exp_id}/start", headers=auth_headers)
    assert started.status_code == 200
    assert started.json()["phase"] == "running"
    return {
        "experiment_id": exp_id,
        "project_id": project["id"],
    }


def _complete(client, auth_headers, exp_id, *, metadata, known_failures=None):
    body = {
        "summary": "trial complete",
        "content_md": "## 实施 log\n基线验证完成。",
        "metadata": metadata,
    }
    if known_failures:
        body["known_failures"] = known_failures
    return client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=auth_headers,
        json=body,
    )


def test_complete_green_pytest_summary_passes(running_experiment, client, auth_headers):
    exp_id = running_experiment["experiment_id"]
    resp = _complete(client, auth_headers, exp_id, metadata={"pytest_summary": _GREEN})
    assert resp.status_code == 200, resp.text
    assert resp.json()["phase"] == "result_review"
    # completion log carries the A4 marker (status passed)
    logs = client.get(f"/api/v1/experiments/{exp_id}/logs", headers=auth_headers).json()
    completion = logs[-1]
    assert completion["metadata_json"]["pytest_summary_validation"]["status"] == "passed"


def test_complete_red_pytest_summary_rejects(running_experiment, client, auth_headers):
    exp_id = running_experiment["experiment_id"]
    resp = _complete(client, auth_headers, exp_id, metadata={"pytest_summary": _RED})
    # StateTransitionError → 422（server/main.py 状态机拒绝映射，与同类门禁一致）
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert "failed=5 > 0" in body["detail"]
    assert "--known-failures" in body["detail"]
    # phase must not have moved
    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()
    assert detail["phase"] == "running"


def test_complete_red_exempted_with_known_failures(
    running_experiment, client, auth_headers
):
    exp_id = running_experiment["experiment_id"]
    resp = _complete(
        client,
        auth_headers,
        exp_id,
        metadata={"pytest_summary": _RED},
        known_failures=["#42", "test_zz_slow"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["phase"] == "result_review"
    logs = client.get(f"/api/v1/experiments/{exp_id}/logs", headers=auth_headers).json()
    completion = logs[-1]
    marker = completion["metadata_json"]["pytest_summary_validation"]
    assert marker["status"] == "exempted"
    assert marker["known_failures"] == ["#42", "test_zz_slow"]
    assert "pytest_summary 机器校验" in completion["content_md"]
    assert "exempted" in completion["content_md"]


def test_complete_total_mismatch_with_ci_baseline_warns(
    running_experiment, client, auth_headers, monkeypatch
):
    """MAP_CI_TEST_TOTAL configured + metadata total mismatch → warning not reject."""
    exp_id = running_experiment["experiment_id"]

    class _StubSettings:
        ci_test_total = 35

    # `get_settings` 在 phase_service 内懒加载（from server.config import ...），
    # 故 patch 定义侧 `server.config.get_settings` 才被后续调用命中。
    monkeypatch.setattr("server.config.get_settings", lambda: _StubSettings())
    green_wrong_total = {"total": 40, "passed": 40, "failed": 0}
    resp = _complete(
        client, auth_headers, exp_id, metadata={"pytest_summary": green_wrong_total}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["phase"] == "result_review"
    logs = client.get(f"/api/v1/experiments/{exp_id}/logs", headers=auth_headers).json()
    completion = logs[-1]
    assert "与 CI 基线 35 不符" in completion["content_md"]
    assert any(
        "与 CI 基线 35 不符" in w
        for w in completion["metadata_json"]["pytest_summary_validation"]["warnings"]
    )


def test_accept_rechecks_pytest_summary_for_reviewer_visibility(
    running_experiment, client, auth_headers, reviewer
):
    """A4 accept path: recheck line lands in the accept log (red light visible)."""
    exp_id = running_experiment["experiment_id"]
    resp = _complete(client, auth_headers, exp_id, metadata={"pytest_summary": _GREEN})
    assert resp.status_code == 200

    accepted = client.post(
        f"/api/v1/experiments/{exp_id}/accept-result",
        headers=reviewer["headers"],
        json={
            "summary": "结果审批通过",
            "content_md": "结果满足计划验收标准",
            "metadata": {"approved": True},
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["phase"] == "done"

    logs = client.get(f"/api/v1/experiments/{exp_id}/logs", headers=auth_headers).json()
    accept_log = logs[-1]
    assert "pytest_summary 机器校验（50cddb7e I4 A4" in accept_log["content_md"]
    recheck = accept_log["metadata_json"]["pytest_summary_validation"]
    assert recheck["status"] == "passed"
