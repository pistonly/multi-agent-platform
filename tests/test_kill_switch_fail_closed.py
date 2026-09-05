"""fs_stop_duplicate_insert flag fail-closed gate + kill switch 端到端测试（实验 M2 I4：A4）。

覆盖：

- flag OFF（M1 默认）→ 行为不变，active transition 通过
- flag ON + 缺 projection 主行 → active transition 409 fail closed
- flag ON + 有 projection 主行 → active transition 放行（lazy
  materialization 不再需要）
- flag ON + terminal transition（done / cancelled）→ 永远不 fail closed
- kill switch：flag ON 时 fail closed → flip OFF → 同一 transition 恢复
  通过（fast rollback 路径）

所有 case 都通过 FastAPI TestClient 走完整 HTTP 路径（project access
check → service gate → state machine → transition），不绕开 API 层。
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from server.domain.models import FsProjection
from tests._frontmatter import make_valid_plan

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _create_project_with_host(
    client: TestClient, admin_headers: dict[str, str]
) -> tuple[dict, dict[str, str]]:
    """新建 project + host agent。返回 (project, host_headers)。

    host agent name 必须以 ``-host`` 结尾，才能被 ``feature_flag_service._ensure_can_set_flag``
    的 persona 解析识别为 host。其他 actor（如 auth_headers）用来跑
    transition。
    """
    project_resp = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={
            "project_key": f"killswitch-{uuid.uuid4().hex[:6]}",
            "name": "KillSwitch Test",
            "workspace_path": "/tmp/killswitch-test",
            "description": "I4 A4 fail-closed gate 端到端",
        },
    )
    assert project_resp.status_code == 201, project_resp.text
    project = project_resp.json()

    host_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": f"{project['project_key']}-host",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert host_resp.status_code == 201, host_resp.text
    host_headers = {"Authorization": f"Bearer {host_resp.json()['api_token']}"}
    return project, host_headers


def _create_test_agent(
    client: TestClient,
    admin_headers: dict[str, str],
    project_key: str,
    *,
    suffix: str,
) -> dict[str, str]:
    """建一个非 host persona（participant）的 agent，仅有 project access。"""
    resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": f"{project_key}-{suffix}",
            "role": "agent",
            "project_key": project_key,
        },
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['api_token']}"}


def _set_flag(
    client: TestClient,
    host_headers: dict[str, str],
    project_id: str,
    value: str,
    *,
    reason: str = "I4 测试：模拟",
) -> None:
    resp = client.put(
        f"/api/v1/projects/{project_id}/feature-flags/fs_stop_duplicate_insert",
        headers=host_headers,
        json={"flag_value": value, "reason": reason},
    )
    assert resp.status_code == 200, resp.text


def _create_experiment(
    client: TestClient,
    headers: dict[str, str],
    project_id: str,
    *,
    mode: str = "direct",
    submit: bool = False,
) -> dict:
    resp = client.post(
        f"/api/v1/projects/{project_id}/experiments",
        headers=headers,
        json={
            "title": "I4 fail-closed gate 实验",
            "plan": {"content_md": make_valid_plan(body="## 目标\nI4 gate 行为")},
            "mode": mode,
            "submit_for_review": submit,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _validate(
    client: TestClient,
    headers: dict[str, str],
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
    headers: dict[str, str],
    experiment_id: str,
    token: str,
    **payload,
):
    return client.post(
        f"/api/v1/experiments/{experiment_id}/transition/commit",
        headers=headers,
        json={"token": token, **payload},
    )


def _phase(client: TestClient, headers: dict[str, str], experiment_id: str) -> str:
    return client.get(
        f"/api/v1/experiments/{experiment_id}", headers=headers
    ).json()["phase"]


def _seed_projection(
    client: TestClient,
    admin_headers: dict[str, str],
    project_id: str,
    tmp_path,
) -> None:
    """通过真实 API 建 projection 主行（绕过 db_session 直写陷阱）。"""
    workspace = tmp_path / "ks-projection-ws"
    workspace.mkdir()
    (workspace / "map").mkdir()

    push_resp = client.put(
        f"/api/v1/projects/{project_id}/fs/projection",
        headers=admin_headers,
        json={
            "client_workspace": str(workspace),
            "content_root": "map",
            "topics": [],
            "experiments": [],
        },
    )
    print("DEBUG push status:", push_resp.status_code, push_resp.text[:300])
    assert push_resp.status_code == 200, push_resp.text


# ---------------------------------------------------------------------------
# 默认行为（flag 缺省 / OFF）：M1 行为不变
# ---------------------------------------------------------------------------


def test_flag_off_default_active_transition_succeeds(
    client: TestClient, admin_headers: dict[str, str]
):
    """flag 完全未 set（默认 OFF）→ active transition 正常推进。

    这条是「flag flip 不破现有 M1 流程」的最基础保证。
    """
    project, _host_headers = _create_project_with_host(client, admin_headers)
    test_headers = _create_test_agent(
        client, admin_headers, project["project_key"], suffix="participant"
    )

    exp = _create_experiment(client, test_headers, project["id"], mode="direct")
    verdict = _validate(client, test_headers, exp["id"], "start").json()
    resp = _commit(client, test_headers, exp["id"], verdict["token"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["phase"] == "running"


def test_flag_explicit_off_active_transition_succeeds(
    client: TestClient, admin_headers: dict[str, str]
):
    """flag 显式 OFF → active transition 正常推进（OFF 语义对齐未 set）。"""
    project, host_headers = _create_project_with_host(client, admin_headers)
    _set_flag(client, host_headers, project["id"], "off")

    test_headers = _create_test_agent(
        client, admin_headers, project["project_key"], suffix="participant"
    )
    exp = _create_experiment(client, test_headers, project["id"], mode="direct")
    verdict = _validate(client, test_headers, exp["id"], "start").json()
    resp = _commit(client, test_headers, exp["id"], verdict["token"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["phase"] == "running"


# ---------------------------------------------------------------------------
# flag ON + 缺 projection 主行 → fail closed（核心证据 2）
# ---------------------------------------------------------------------------


def test_flag_on_active_transition_fails_closed_without_projection(
    client: TestClient, admin_headers: dict[str, str]
):
    """flag ON + 缺 projection → active transition 409 + 完整修复指引。

    这是 I4 第二条实测证据（active 实验人为缺 projection 主行 →
    fail closed）。
    """
    project, host_headers = _create_project_with_host(client, admin_headers)
    _set_flag(
        client,
        host_headers,
        project["id"],
        "on",
        reason="I4 测试：缺 projection fail closed",
    )

    test_headers = _create_test_agent(
        client, admin_headers, project["project_key"], suffix="participant"
    )
    exp = _create_experiment(client, test_headers, project["id"], mode="direct")

    verdict = _validate(client, test_headers, exp["id"], "start")
    assert verdict.status_code == 409, verdict.text
    detail = verdict.json()["detail"]
    # 错误文案必须包含 flag 名 + projection 缺失 + 推荐路径
    assert "fs_stop_duplicate_insert" in detail
    assert "projection" in detail.lower()
    assert "fail closed" in detail.lower()
    assert "map sync publish --full" in detail
    assert "kill switch" in detail.lower()
    # fail closed 不落地任何状态
    assert _phase(client, test_headers, exp["id"]) == "draft"


# ---------------------------------------------------------------------------
# flag ON + 有 projection 主行 → 放行（lazy materialization 不再需要）
# ---------------------------------------------------------------------------


def test_flag_on_active_transition_succeeds_when_projection_present(
    client: TestClient,
    admin_headers: dict[str, str],
    db_session,
    tmp_path,
):
    """flag ON + 有 projection 主行 → 放行（与 default 行为一致）。"""
    from sqlalchemy import select

    project, host_headers = _create_project_with_host(client, admin_headers)
    _set_flag(
        client,
        host_headers,
        project["id"],
        "on",
        reason="I4 测试：projection 主行已建立",
    )
    _seed_projection(client, admin_headers, project["id"], tmp_path)

    # db_session 用正确的 select 确认 projection 行可见（gate 的查询路径）
    proj_row = db_session.scalar(
        select(FsProjection).where(FsProjection.project_id == uuid.UUID(project["id"]))
    )
    assert proj_row is not None, "db_session 看不到刚 push 的 projection"

    test_headers = _create_test_agent(
        client, admin_headers, project["project_key"], suffix="participant"
    )
    exp = _create_experiment(client, test_headers, project["id"], mode="direct")

    verdict = _validate(client, test_headers, exp["id"], "start").json()
    resp = _commit(client, test_headers, exp["id"], verdict["token"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["phase"] == "running"


# ---------------------------------------------------------------------------
# terminal transition（→ done / cancelled）永远不被 fail closed 拦截
# ---------------------------------------------------------------------------


def test_flag_on_terminal_cancel_never_fail_closed(
    client: TestClient, admin_headers: dict[str, str]
):
    """flag ON + 缺 projection + cancel transition → 仍可 cancel（terminal）。

    cancel 是 host 唯一可写的 terminal transition；M2 A4 明确只 gate
    active phase（terminal 不被拦截），保证 host 任何时候都能 cancel
    出问题实验。本用例把实验直接放在 review 状态（submit_for_review=True
    让 create 一并入 review），从 review cancel 到 cancelled——这是
    唯一路径里完全跳过 active gate 的 terminal 跳转。
    """
    project, host_headers = _create_project_with_host(client, admin_headers)
    _set_flag(client, host_headers, project["id"], "on", reason="I4：测 terminal")

    test_headers = _create_test_agent(
        client, admin_headers, project["project_key"], suffix="participant"
    )
    # standard 模式下 submit_for_review=True 把实验直接送进 review，
    # 绕开 start → running 这个会触发 fail closed 的 active transition。
    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=test_headers,
        json={
            "title": "I4 cancel 路径实验",
            "plan": {"content_md": make_valid_plan(body="## 目标\n验证 cancel 不被 gate")},
            "mode": "standard",
            "submit_for_review": True,
        },
    )
    assert resp.status_code == 201, resp.text
    exp = resp.json()
    assert exp["phase"] == "review", exp

    # 从 review 直接 cancel（review→cancelled 是 terminal 跳转）。
    # cancel 需要 creator / admin；creator 是 participant（test_headers）。
    cancel_resp = client.post(
        f"/api/v1/experiments/{exp['id']}/cancel", headers=test_headers
    )
    assert cancel_resp.status_code == 200, cancel_resp.text
    assert cancel_resp.json()["phase"] == "cancelled"


# ---------------------------------------------------------------------------
# Kill switch：flag ON → fail closed；flip OFF → 同一 transition 恢复通过
# ---------------------------------------------------------------------------


def test_kill_switch_recovery_after_flip_off(
    client: TestClient, admin_headers: dict[str, str]
):
    """kill switch 触发回退旧写路径：flag ON → fail closed → flip OFF → 恢复。

    这是 I4 第一条实测证据（kill switch 触发回退旧写路径）。同一实验
    同一 transition：先在 flag=ON + 缺 projection 时 409 fail closed；
    再把 flag flip off 后直接走通 commit。
    """
    project, host_headers = _create_project_with_host(client, admin_headers)
    test_headers = _create_test_agent(
        client, admin_headers, project["project_key"], suffix="participant"
    )
    exp = _create_experiment(client, test_headers, project["id"], mode="direct")

    # 1) flag=ON 时 fail closed
    _set_flag(client, host_headers, project["id"], "on", reason="I4：先测 fail closed")
    blocked = _validate(client, test_headers, exp["id"], "start")
    assert blocked.status_code == 409, blocked.text
    assert _phase(client, test_headers, exp["id"]) == "draft"

    # 2) kill switch：flip OFF（fast rollback，reason 可空）
    off_resp = client.put(
        f"/api/v1/projects/{project['id']}/feature-flags/fs_stop_duplicate_insert",
        headers=host_headers,
        json={"flag_value": "off", "reason": "线上 duplicate-INSERT 漂移触发回退"},
    )
    assert off_resp.status_code == 200, off_resp.text
    assert off_resp.json()["flag_value"] == "off"

    # 3) 同一 transition 现在通过
    recovered = _validate(client, test_headers, exp["id"], "start").json()
    commit_resp = _commit(client, test_headers, exp["id"], recovered["token"])
    assert commit_resp.status_code == 200, commit_resp.text
    assert commit_resp.json()["phase"] == "running"


# ---------------------------------------------------------------------------
# 权限：非 host / 非 admin 不能 set flag
# ---------------------------------------------------------------------------


def test_set_flag_forbidden_for_participant_actor(
    client: TestClient, admin_headers: dict[str, str]
):
    """participant 直接 PUT flag → 403 ForbiddenError（不在 host/admin 白名单）。"""
    project, _host_headers = _create_project_with_host(client, admin_headers)
    test_headers = _create_test_agent(
        client, admin_headers, project["project_key"], suffix="participant"
    )

    resp = client.put(
        f"/api/v1/projects/{project['id']}/feature-flags/fs_stop_duplicate_insert",
        headers=test_headers,
        json={"flag_value": "on", "reason": "试图越权"},
    )
    assert resp.status_code == 403, resp.text
    assert "host or admin" in resp.json()["detail"]


def test_set_flag_rejects_unknown_value(
    client: TestClient, admin_headers: dict[str, str]
):
    """set 非法 value（不在 {on, off}）→ 422 pydantic validation。"""
    project, host_headers = _create_project_with_host(client, admin_headers)

    resp = client.put(
        f"/api/v1/projects/{project['id']}/feature-flags/fs_stop_duplicate_insert",
        headers=host_headers,
        json={"flag_value": "true", "reason": "test"},
    )
    assert resp.status_code == 422, resp.text


def test_set_flag_on_requires_non_empty_reason(
    client: TestClient, admin_headers: dict[str, str]
):
    """set on 时 reason 为空 → 400 ValueError（domain handler 翻译）。"""
    project, host_headers = _create_project_with_host(client, admin_headers)

    for bad_reason in (None, "", "   "):
        resp = client.put(
            f"/api/v1/projects/{project['id']}/feature-flags/fs_stop_duplicate_insert",
            headers=host_headers,
            json={"flag_value": "on", "reason": bad_reason},
        )
        assert resp.status_code == 400, resp.text
        assert "non-empty reason" in resp.text
