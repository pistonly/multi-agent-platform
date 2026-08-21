"""远程 / 容器部署模式（server 看不到 workspace）的 FS plane 三步改造：

1. 部署矩阵显式化：``GET /fs/status`` 三态（local-fs / projection-cache /
   detached），detached 时 hint 带修复指引。
2. 验证型写拆分：validate（evidence + ack 校验 + HMAC token）→ 客户端本地
   写回 index.md → commit（审计 + 投影缓存刷新）。
3. 读侧投影上行：``PUT /fs/projection``（map fs push）后，/topics 合并、
   /topics/{uuid} 详情、/agents/me/work 全部回退到投影缓存。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from map_fs import scan_plane, update_topic_index, write_round_comment, write_topic_index
from sqlalchemy import select

from server.domain.models import AuditLog
from server.services import fs_write_token
from server.services.fs_write_token import FsWriteTokenError

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _create_project(client, admin_headers: dict, workspace: Path, key: str) -> dict:
    response = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={
            "project_key": key,
            "name": key,
            "workspace_path": str(workspace),
        },
    )
    assert response.status_code == 201
    return response.json()


def _detail_read(topic) -> dict:
    from cli.commands.fs import fs_topic_to_detail_read

    return fs_topic_to_detail_read(topic).model_dump(mode="json")


def _push_plane(client, headers: dict, pid: str, workspace: Path) -> dict:
    plane = scan_plane(workspace)
    response = client.put(
        f"/api/v1/projects/{pid}/fs/projection",
        headers=headers,
        json={
            "client_workspace": str(workspace),
            "topics": [_detail_read(t) for t in plane.topics],
            "experiments": [],
        },
    )
    assert response.status_code == 200
    return response.json()


def _seed_topic(workspace: Path, slug: str = "remote-demo") -> None:
    write_topic_index(
        workspace, slug, title="Remote Demo", creator="host", participants=["participant"]
    )
    write_round_comment(workspace, slug, round_number=1, persona="host", body="# r1 host")
    write_round_comment(
        workspace, slug, round_number=1, persona="participant", body="# r1 part"
    )


# ---------------------------------------------------------------------------
# 1. 部署矩阵：fs/status 三态
# ---------------------------------------------------------------------------


def test_fs_status_three_modes(client, admin_headers: dict, tmp_path: Path) -> None:
    local_ws = tmp_path / "local-ws"
    local_ws.mkdir()
    (local_ws / "map").mkdir()
    local = _create_project(client, admin_headers, local_ws, f"fs-local-{uuid.uuid4().hex[:6]}")

    resp = client.get(f"/api/v1/projects/{local['id']}/fs/status", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "local-fs"
    assert body["content_root_exists"] is True
    assert body["hint"] == ""

    detached = _create_project(
        client, admin_headers, tmp_path / "gone", f"fs-gone-{uuid.uuid4().hex[:6]}"
    )
    resp = client.get(f"/api/v1/projects/{detached['id']}/fs/status", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "detached"
    assert body["workspace_exists"] is False
    assert "map fs push" in body["hint"]

    _push_plane(client, admin_headers, detached["id"], local_ws)
    resp = client.get(f"/api/v1/projects/{detached['id']}/fs/status", headers=admin_headers)
    body = resp.json()
    assert body["mode"] == "projection-cache"
    assert body["projection_pushed_at"] is not None


# ---------------------------------------------------------------------------
# 2. 验证型写：validate → 本地写回 → commit（远程模式）
# ---------------------------------------------------------------------------


def test_remote_advance_validate_local_write_commit(
    client, admin_headers, db_session, tmp_path
):
    """完整链路：evidence 校验 → 签发 → 本地写 index.md → commit 审计 + 刷投影。"""
    client_ws = tmp_path / "client-ws"
    client_ws.mkdir()
    _seed_topic(client_ws)
    project = _create_project(
        client, admin_headers, tmp_path / "server-cannot-see", f"fs-rmt-{uuid.uuid4().hex[:6]}"
    )
    pid = project["id"]
    _push_plane(client, admin_headers, pid, client_ws)

    topic = scan_plane(client_ws).topic_by_slug("remote-demo")
    assert topic is not None

    # ack 满员（r1 host + participant 都有文件）→ verdict round2
    resp = client.post(
        f"/api/v1/projects/{pid}/fs/topics/remote-demo/advance-round/validate",
        headers=admin_headers,
        json={"evidence": _detail_read(topic)},
    )
    assert resp.status_code == 200
    verdict = resp.json()
    assert verdict["fields"] == {"round": "round2"}
    assert verdict["token"]

    # 模拟 CLI：本地写回 index.md
    update_topic_index(client_ws, "remote-demo", round="round2")

    resp = client.post(
        f"/api/v1/projects/{pid}/fs/write-commit",
        headers=admin_headers,
        json={
            "token": verdict["token"],
            "slug": "remote-demo",
            "action": "advance-round",
            "applied_fields": verdict["fields"],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True

    # 审计落库（TestClient 与测试共享 db_session）
    actions = [
        row.action
        for row in db_session.scalars(
            select(AuditLog).where(AuditLog.project_id == uuid.UUID(pid))
        ).all()
    ]
    assert "topic.advance_round" in actions

    # commit 顺带刷新投影：列表直接可见 round2（无需再次 push）
    merged = client.get(f"/api/v1/projects/{pid}/topics", headers=admin_headers).json()
    hit = next(t for t in merged if t.get("slug") == "remote-demo")
    assert hit["discussion_round"] == "round2"


def test_remote_advance_ack_pending(client, admin_headers, tmp_path):
    """participant 未发言 → validate 409 + missing 列表（evidence 校验）。"""
    client_ws = tmp_path / "client-ws2"
    client_ws.mkdir()
    write_topic_index(
        client_ws, "ack-pending", title="Ack", creator="host", participants=["participant"]
    )
    write_round_comment(client_ws, "ack-pending", round_number=1, persona="host", body="# r1")
    write_round_comment(client_ws, "ack-pending", round_number=1, persona="participant", body="# p")
    update_topic_index(client_ws, "ack-pending", round="round2")

    project = _create_project(
        client, admin_headers, tmp_path / "nowhere", f"fs-ack-{uuid.uuid4().hex[:6]}"
    )
    topic = scan_plane(client_ws).topic_by_slug("ack-pending")
    assert topic is not None

    resp = client.post(
        f"/api/v1/projects/{project['id']}/fs/topics/ack-pending/advance-round/validate",
        headers=admin_headers,
        json={"evidence": _detail_read(topic)},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "round_ack_pending"
    assert resp.json()["detail"]["missing"] == ["participant"]


def test_remote_validate_without_evidence_or_projection(client, admin_headers, tmp_path):
    """detached 且无 evidence / 投影：显式 409 + fs_plane_unavailable，不再静默 404。"""
    project = _create_project(
        client, admin_headers, tmp_path / "void", f"fs-void-{uuid.uuid4().hex[:6]}"
    )
    resp = client.post(
        f"/api/v1/projects/{project['id']}/fs/topics/any/advance-round/validate",
        headers=admin_headers,
        json={},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "fs_plane_unavailable"

    # 旧服务端写回端点在远程模式下同样显式失败
    resp = client.post(
        f"/api/v1/projects/{project['id']}/fs/topics/any/advance-round",
        headers=admin_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "fs_plane_unavailable"


def test_commit_rejects_forged_and_expired_tokens(client, admin_headers, tmp_path):
    client_ws = tmp_path / "client-ws3"
    client_ws.mkdir()
    _seed_topic(client_ws)
    project = _create_project(
        client, admin_headers, tmp_path / "far-away", f"fs-tok-{uuid.uuid4().hex[:6]}"
    )
    pid = project["id"]

    # 伪造 token
    resp = client.post(
        f"/api/v1/projects/{pid}/fs/write-commit",
        headers=admin_headers,
        json={"token": "v1.bogus.bogus", "slug": "remote-demo", "action": "advance-round"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "fs_write_token_invalid"

    # 过期 token（直接用服务签名函数构造 ttl=-1）
    token, _ = fs_write_token.sign_write_token(
        action="advance-round", project_id=pid, slug="remote-demo",
        fields={"round": "round2"}, ttl_seconds=-1,
    )
    resp = client.post(
        f"/api/v1/projects/{pid}/fs/write-commit",
        headers=admin_headers,
        json={"token": token, "slug": "remote-demo", "action": "advance-round"},
    )
    assert resp.status_code == 401

    # action 与 token 绑定不符（advance token + close action）
    token2, _ = fs_write_token.sign_write_token(
        action="advance-round", project_id=pid, slug="remote-demo",
        fields={"round": "round2"},
    )
    resp = client.post(
        f"/api/v1/projects/{pid}/fs/write-commit",
        headers=admin_headers,
        json={"token": token2, "slug": "remote-demo", "action": "close"},
    )
    assert resp.status_code == 401


def test_remote_close_validate_commit(client, admin_headers, tmp_path):
    client_ws = tmp_path / "client-ws4"
    client_ws.mkdir()
    _seed_topic(client_ws)
    project = _create_project(
        client, admin_headers, tmp_path / "far-away2", f"fs-close-{uuid.uuid4().hex[:6]}"
    )
    pid = project["id"]
    _push_plane(client, admin_headers, pid, client_ws)
    topic = scan_plane(client_ws).topic_by_slug("remote-demo")
    assert topic is not None

    resp = client.post(
        f"/api/v1/projects/{pid}/fs/topics/remote-demo/close/validate",
        headers=admin_headers,
        json={"close_reason": "no_experiment_needed", "evidence": _detail_read(topic)},
    )
    assert resp.status_code == 200
    verdict = resp.json()
    assert verdict["fields"]["status"] == "closed"
    assert verdict["fields"]["close_reason"] == "no_experiment_needed"

    update_topic_index(client_ws, "remote-demo", status="closed")
    resp = client.post(
        f"/api/v1/projects/{pid}/fs/write-commit",
        headers=admin_headers,
        json={
            "token": verdict["token"],
            "slug": "remote-demo",
            "action": "close",
            "applied_fields": verdict["fields"],
        },
    )
    assert resp.status_code == 200

    # 投影缓存已同步 close
    merged = client.get(f"/api/v1/projects/{pid}/topics", headers=admin_headers).json()
    hit = next(t for t in merged if t.get("slug") == "remote-demo")
    assert hit["status"] == "closed"


# ---------------------------------------------------------------------------
# 3. 读侧回退：projection → /topics 合并 / 详情 / work 待办
# ---------------------------------------------------------------------------


def test_projection_read_fallback(client, admin_headers, tmp_path):
    client_ws = tmp_path / "client-ws5"
    client_ws.mkdir()
    _seed_topic(client_ws)
    write_round_comment(client_ws, "remote-demo", round_number=2, persona="host", body="# r2")
    project = _create_project(
        client, admin_headers, tmp_path / "unreachable", f"fs-fb-{uuid.uuid4().hex[:6]}"
    )
    pid = project["id"]
    _push_plane(client, admin_headers, pid, client_ws)

    # /topics 合并
    merged = client.get(f"/api/v1/projects/{pid}/topics", headers=admin_headers).json()
    slugs = [t.get("slug") for t in merged]
    assert "remote-demo" in slugs

    # 确定性 id 详情（含评论正文，Web UI 渲染源）
    hit = next(t for t in merged if t.get("slug") == "remote-demo")
    detail = client.get(f"/api/v1/topics/{hit['id']}", headers=admin_headers)
    assert detail.status_code == 200
    body = detail.json()
    assert [c["author_name"] for c in body["comments"]] == ["host", "participant", "host"]
    assert "r2" in body["comments"][-1]["body"]

    # 专用 fs 端点同样回退
    fs_list = client.get(f"/api/v1/projects/{pid}/fs/topics", headers=admin_headers).json()
    assert [t["slug"] for t in fs_list] == ["remote-demo"]


def test_work_projection_fallback(client, admin_headers, tmp_path):
    """waker 源（/agents/me/work）在远程模式下从投影推导 FS 待办。"""
    client_ws = tmp_path / "client-ws6"
    client_ws.mkdir()
    _seed_topic(client_ws)
    # 推进到 round2：participant 未发言 → pending_topic_reply
    update_topic_index(client_ws, "remote-demo", round="round2")
    project = _create_project(
        client, admin_headers, tmp_path / "no-view", f"fs-work-{uuid.uuid4().hex[:6]}"
    )
    pid = project["id"]
    _push_plane(client, admin_headers, pid, client_ws)

    # persona agent：名字必须是 multi-agent-platform-participant（persona 映射）
    agent_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": "multi-agent-platform-participant",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert agent_resp.status_code == 201
    part_headers = {"Authorization": f"Bearer {agent_resp.json()['api_token']}"}

    work = client.get("/api/v1/agents/me/work", headers=part_headers).json()
    progress = (work.get("topic_progress") or {}).get("items") or []
    slugs = [p.get("topic_title") for p in progress]
    assert "Remote Demo" in slugs
    item = next(p for p in progress if p.get("topic_title") == "Remote Demo")
    kinds = [w["kind"] for w in item["work_items"]]
    assert "pending_topic_reply" in kinds


# ---------------------------------------------------------------------------
# 同机部署（local-fs）下的 validate/commit 复核路径
# ---------------------------------------------------------------------------


def test_local_mode_commit_checks_written_file(client, admin_headers, tmp_path):
    ws = tmp_path / "local-ws-x"
    ws.mkdir()
    _seed_topic(ws)
    project = _create_project(client, admin_headers, ws, f"fs-lcl-{uuid.uuid4().hex[:6]}")
    pid = project["id"]

    resp = client.post(
        f"/api/v1/projects/{pid}/fs/topics/remote-demo/advance-round/validate",
        headers=admin_headers,
        json={},
    )
    assert resp.status_code == 200
    verdict = resp.json()

    # 未写回就 commit → 409 fs_write_not_applied（同机复核生效）
    resp = client.post(
        f"/api/v1/projects/{pid}/fs/write-commit",
        headers=admin_headers,
        json={
            "token": verdict["token"],
            "slug": "remote-demo",
            "action": "advance-round",
            "applied_fields": verdict["fields"],
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "fs_write_not_applied"

    # 写回后 commit → 200
    update_topic_index(ws, "remote-demo", round="round2")
    resp = client.post(
        f"/api/v1/projects/{pid}/fs/write-commit",
        headers=admin_headers,
        json={
            "token": verdict["token"],
            "slug": "remote-demo",
            "action": "advance-round",
            "applied_fields": verdict["fields"],
        },
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# token 单元：签发/验证/归属
# ---------------------------------------------------------------------------


def test_write_token_roundtrip_and_binding():
    token, expires = fs_write_token.sign_write_token(
        action="close", project_id="00000000-0000-0000-0000-000000000001",
        slug="t", fields={"status": "closed"},
    )
    payload = fs_write_token.verify_write_token(
        token,
        action="close",
        project_id="00000000-0000-0000-0000-000000000001",
        slug="t",
    )
    assert payload["fields"] == {"status": "closed"}
    assert expires is not None

    # 归属校验：换 project / slug / action 都要拒绝
    with pytest.raises(FsWriteTokenError):
        fs_write_token.verify_write_token(
            token, action="close",
            project_id="00000000-0000-0000-0000-000000000002", slug="t",
        )
    with pytest.raises(FsWriteTokenError):
        fs_write_token.verify_write_token(
            token, action="advance-round",
            project_id="00000000-0000-0000-0000-000000000001", slug="t",
        )
    with pytest.raises(FsWriteTokenError):
        fs_write_token.verify_write_token(
            token, action="close",
            project_id="00000000-0000-0000-0000-000000000001", slug="other",
        )
