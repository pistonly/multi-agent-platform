"""实验B（24f3e565）server 端 lifecycle transition 协议测试。

覆盖验收标准的服务器侧证据：

- B1 validate 签发七元组绑定 token、validate 不落地状态、
  token 归属/过期 commit 拒绝且不落地任何状态。
- B2 重放幂等：同 token 重放 commit 返回原 receipt，不重复 audit/通知。
- B5 CAS：并发 cancel/complete 首个成功提交者胜出，败方 409 带胜出
  receipt 证据，服务器状态只反映胜者。
- B7 无旁路：单体端点（cancel 等）与两跳协议同构——都产生 receipt；
  direct 被委派 executor 两跳 complete 与 host cancel 走同一原语。
- B8 指纹 fail closed：validate 与 commit 双侧，读路径不受影响。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from server.domain.models import AuditLog, Notification
from tests._frontmatter import make_valid_plan

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _create_experiment(
    client: TestClient,
    headers: dict,
    project_id: str,
    *,
    mode: str = "standard",
    submit: bool = False,
) -> dict:
    resp = client.post(
        f"/api/v1/projects/{project_id}/experiments",
        headers=headers,
        json={
            "title": "transition 协议实验",
            "plan": {"content_md": make_valid_plan(body="## 目标\n验证协议"), "change_note": "v1"},
            "mode": mode,
            "submit_for_review": submit,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _validate(
    client: TestClient,
    headers: dict,
    experiment_id: str,
    action: str,
    **extra,
):
    return client.post(
        f"/api/v1/experiments/{experiment_id}/transition/validate",
        headers=headers,
        json={"action": action, **extra},
    )


def _commit(
    client: TestClient,
    headers: dict,
    experiment_id: str,
    token: str,
    **payload,
):
    return client.post(
        f"/api/v1/experiments/{experiment_id}/transition/commit",
        headers=headers,
        json={"token": token, **payload},
    )


def _phase(client: TestClient, headers: dict, experiment_id: str) -> str:
    return client.get(
        f"/api/v1/experiments/{experiment_id}", headers=headers
    ).json()["phase"]


def _real_workspace_project(client: TestClient, admin_headers: dict, key: str, path) -> dict:
    resp = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={
            "project_key": key,
            "name": f"Fingerprint {key}",
            "workspace_path": str(path),
            "description": "B8 指纹门禁",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# B1：七元组 token / validate 不落地 / 不匹配与过期拒绝
# ---------------------------------------------------------------------------


def test_validate_signs_tuple_token_without_mutating_state(
    client, auth_headers, project
):
    exp = _create_experiment(client, auth_headers, project["id"], mode="direct")
    before = _phase(client, auth_headers, exp["id"])
    assert before == "draft"

    resp = _validate(client, auth_headers, exp["id"], "start")
    assert resp.status_code == 200, resp.text
    verdict = resp.json()
    # 七元组回显（actor 绑定在 token 里，不在 verdict 回显字段中）
    assert verdict["action"] == "start"
    assert verdict["experiment_id"] == exp["id"]
    assert verdict["project_id"] == project["id"]
    assert verdict["from_phase"] == "draft"
    assert verdict["to_phase"] == "running"
    assert verdict["base_revision"] == 0
    assert verdict["token"] and verdict["nonce"]
    # 有限 TTL
    expires_at = datetime.fromisoformat(verdict["expires_at"].replace("Z", "+00:00"))
    remaining = expires_at - datetime.now(timezone.utc)
    assert timedelta(0) < remaining <= timedelta(seconds=600)

    # validate 不落地任何状态
    assert _phase(client, auth_headers, exp["id"]) == "draft"


def test_commit_rejects_token_bound_to_other_experiment(client, auth_headers, project):
    exp_a = _create_experiment(client, auth_headers, project["id"], mode="direct")
    exp_b = _create_experiment(client, auth_headers, project["id"], mode="direct")
    verdict = _validate(client, auth_headers, exp_a["id"], "start").json()

    resp = _commit(client, auth_headers, exp_b["id"], verdict["token"])
    assert resp.status_code == 409, resp.text
    # 双方都不落地
    assert _phase(client, auth_headers, exp_a["id"]) == "draft"
    assert _phase(client, auth_headers, exp_b["id"]) == "draft"


def test_commit_rejects_expired_token(
    client, auth_headers, project, monkeypatch
):
    from server.services import experiment_transition_token as token_mod

    exp = _create_experiment(client, auth_headers, project["id"], mode="direct")
    verdict = _validate(client, auth_headers, exp["id"], "start").json()

    class _ShiftedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ARG003
            return datetime.now(tz) + timedelta(hours=2)

    monkeypatch.setattr(token_mod, "datetime", _ShiftedDatetime)
    resp = _commit(client, auth_headers, exp["id"], verdict["token"])
    assert resp.status_code == 409, resp.text
    assert "expired" in resp.json()["detail"]
    assert _phase(client, auth_headers, exp["id"]) == "draft"


# ---------------------------------------------------------------------------
# B2：重放幂等
# ---------------------------------------------------------------------------


def test_replay_commit_returns_same_receipt_without_reemitting(
    client, auth_headers, project, db_session
):
    exp = _create_experiment(client, auth_headers, project["id"], mode="direct")
    verdict = _validate(client, auth_headers, exp["id"], "start").json()

    first = _commit(client, auth_headers, exp["id"], verdict["token"])
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["accepted"] is True
    assert body["replayed"] is False
    receipt = body["receipt"]
    assert receipt["nonce"] == verdict["nonce"]
    assert body["phase"] == "running"

    audit_actions = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "experiment.phase_changed")
        .count()
    )
    notification_count = db_session.query(Notification).count()

    replay = _commit(client, auth_headers, exp["id"], verdict["token"])
    assert replay.status_code == 200, replay.text
    replayed = replay.json()
    assert replayed["accepted"] is True
    assert replayed["replayed"] is True
    # 同 receipt 原样返回
    assert replayed["receipt"]["nonce"] == receipt["nonce"]
    assert replayed["receipt"]["committed_at"] == receipt["committed_at"]
    assert replayed["receipt"]["token_digest"] == receipt["token_digest"]
    # 不重复写 audit / 发通知 / 变更状态
    assert (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "experiment.phase_changed")
        .count()
        == audit_actions
    )
    assert db_session.query(Notification).count() == notification_count
    assert replayed["phase"] == "running"


# ---------------------------------------------------------------------------
# B5：CAS 首个成功提交者胜出
# ---------------------------------------------------------------------------


def test_cas_first_committer_wins_loser_gets_winner_evidence(
    client, auth_headers, admin_headers, project
):
    exp = _create_experiment(client, auth_headers, project["id"], mode="direct")
    winner_token = _validate(client, auth_headers, exp["id"], "start").json()["token"]
    loser_token = _validate(client, admin_headers, exp["id"], "start").json()["token"]

    win = _commit(client, auth_headers, exp["id"], winner_token)
    assert win.status_code == 200, win.text

    lose = _commit(client, admin_headers, exp["id"], loser_token)
    assert lose.status_code == 409, lose.text
    detail = lose.json()["detail"]
    assert "CAS lost" in detail
    # 败方收到当前 phase 与胜出 receipt 证据（B5/B4）
    assert "phase=running" in detail
    assert "胜出 receipt" in detail and "token_digest=" in detail
    # 服务器状态只反映胜者
    assert _phase(client, admin_headers, exp["id"]) == "running"


# ---------------------------------------------------------------------------
# B7：无旁路——单体端点与两跳协议同构
# ---------------------------------------------------------------------------


def test_monolithic_endpoint_goes_through_same_primitive(
    client, auth_headers, project
):
    exp = _create_experiment(client, auth_headers, project["id"], submit=True)
    assert exp["phase"] == "review"

    # 单体 cancel 端点：Web/旧 SDK 单跳语义不变
    resp = client.post(
        f"/api/v1/experiments/{exp['id']}/cancel", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["phase"] == "cancelled"

    # 但同样留下 receipt——单体只是 validate/commit 原语的薄包装（无旁路）
    receipts = client.get(
        f"/api/v1/experiments/{exp['id']}/transition/receipts",
        headers=auth_headers,
    ).json()
    assert [r["action"] for r in receipts] == ["cancel"]
    assert receipts[0]["from_phase"] == "review"
    assert receipts[0]["to_phase"] == "cancelled"


def test_direct_complete_by_delegated_executor_uses_same_primitive(
    client, auth_headers, reviewer, project
):
    exp = _create_experiment(client, auth_headers, project["id"], mode="direct")
    start = _validate(client, auth_headers, exp["id"], "start")
    assert start.status_code == 200
    start_commit = _commit(
        client,
        auth_headers,
        exp["id"],
        start.json()["token"],
        start={"executor_agent_id": reviewer["id"]},
    )
    assert start_commit.status_code == 200, start_commit.text
    assert start_commit.json()["executor_agent_id"] == reviewer["id"]

    # 非被委派者不能 complete（validate 侧角色 gate）
    denied = _validate(client, auth_headers, exp["id"], "complete")
    assert denied.status_code == 403, denied.text

    # 被委派 executor 两跳 complete——与 host cancel 走同一 validate/commit 原语
    v = _validate(client, reviewer["headers"], exp["id"], "complete")
    assert v.status_code == 200, v.text
    c = _commit(
        client,
        reviewer["headers"],
        exp["id"],
        v.json()["token"],
        complete={"summary": "direct 完成", "content_md": "## 结果\n全绿"},
    )
    assert c.status_code == 200, c.text
    body = c.json()
    assert body["receipt"]["action"] == "complete"
    assert body["receipt"]["to_phase"] == "done"
    assert body["phase"] == "done"

    receipts = client.get(
        f"/api/v1/experiments/{exp['id']}/transition/receipts",
        headers=reviewer["headers"],
    ).json()
    assert [r["action"] for r in receipts] == ["complete", "start"]


# ---------------------------------------------------------------------------
# B8：指纹 fail closed（写路径双侧；读路径不受影响）
# ---------------------------------------------------------------------------


def test_validate_fingerprint_mismatch_fails_closed(
    client, admin_headers, tmp_path
):
    from server.services.experiment_transition_service import workspace_fingerprint_of

    proj = _real_workspace_project(client, admin_headers, "fp-proj-1", tmp_path)
    exp = _create_experiment(client, admin_headers, proj["id"], mode="direct")

    wrong = _validate(
        client, admin_headers, exp["id"], "start", workspace_fingerprint="dev=999:ino=999"
    )
    assert wrong.status_code == 409, wrong.text
    assert "fingerprint mismatch" in wrong.json()["detail"]
    assert "rebind" in wrong.json()["detail"]
    # fail closed 不落地
    assert _phase(client, admin_headers, exp["id"]) == "draft"

    right = _validate(
        client,
        admin_headers,
        exp["id"],
        "start",
        workspace_fingerprint=workspace_fingerprint_of(str(tmp_path)),
    )
    assert right.status_code == 200, right.text


def test_commit_restats_fingerprint_and_blocks_workspace_swap(
    client, admin_headers, tmp_path, monkeypatch
):
    from server.services import experiment_transition_service as svc_mod
    from server.services.experiment_transition_service import workspace_fingerprint_of

    proj = _real_workspace_project(client, admin_headers, "fp-proj-2", tmp_path)
    exp = _create_experiment(client, admin_headers, proj["id"], mode="direct")
    verdict = _validate(
        client,
        admin_headers,
        exp["id"],
        "start",
        workspace_fingerprint=workspace_fingerprint_of(str(tmp_path)),
    ).json()

    # validate 之后 workspace 被调包（server 侧 re-stat 结果变化）
    monkeypatch.setattr(svc_mod, "workspace_fingerprint_of", lambda _p: "dev=1:ino=1")
    resp = _commit(client, admin_headers, exp["id"], verdict["token"])
    assert resp.status_code == 409, resp.text
    assert "mismatch at commit" in resp.json()["detail"]
    assert _phase(client, admin_headers, exp["id"]) == "draft"


# ---------------------------------------------------------------------------
# receipt 查询（recover 的证据源，B3/B4）
# ---------------------------------------------------------------------------


def test_receipt_lookup_endpoints(client, auth_headers, project):
    exp = _create_experiment(client, auth_headers, project["id"], mode="direct")
    verdict = _validate(client, auth_headers, exp["id"], "start").json()
    _commit(client, auth_headers, exp["id"], verdict["token"])

    listed = client.get(
        f"/api/v1/experiments/{exp['id']}/transition/receipts",
        headers=auth_headers,
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    got = client.get(
        f"/api/v1/experiments/{exp['id']}/transition/receipts/{verdict['nonce']}",
        headers=auth_headers,
    )
    assert got.status_code == 200
    assert got.json()["action"] == "start"

    missing = client.get(
        f"/api/v1/experiments/{exp['id']}/transition/receipts/nonexistent-nonce",
        headers=auth_headers,
    )
    assert missing.status_code == 404
