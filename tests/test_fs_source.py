"""fs plane：map/ 文件夹事实源（解析器 + API 合并 + 验证型写 + work 推导）。"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from map_fs import (
    derive_work,
    scan_plane,
    topic_id_for_slug,
    update_topic_index,
    write_round_comment,
    write_topic_index,
)

# ---------------------------------------------------------------------------
# 解析器（纯函数）
# ---------------------------------------------------------------------------


def test_write_and_parse_roundtrip(tmp_path: Path) -> None:
    write_topic_index(tmp_path, "fs-demo", title="FS Demo", creator="host", description="背景说明")
    write_round_comment(tmp_path, "fs-demo", round_number=1, persona="host", body="# Host 观点\n正文A")
    write_round_comment(
        tmp_path, "fs-demo", round_number=1, persona="participant", body="# 参与者观点\n正文B"
    )

    plane = scan_plane(tmp_path)
    assert len(plane.topics) == 1
    topic = plane.topics[0]
    assert topic.slug == "fs-demo"
    assert topic.status == "open"
    assert topic.round == "round1"
    assert topic.title == "FS Demo"
    assert topic.id == topic_id_for_slug("fs-demo")
    assert [c.author for c in topic.comments] == ["host", "participant"]
    assert all(c.round == 1 for c in topic.comments)
    assert topic.comments[0].excerpt == "Host 观点"
    assert topic.participants == ["host", "participant"]


def test_comment_file_is_immutable_by_default(tmp_path: Path) -> None:
    write_topic_index(tmp_path, "x", title="X", creator="host")
    write_round_comment(tmp_path, "x", round_number=1, persona="host", body="first")
    with pytest.raises(FileExistsError):
        write_round_comment(tmp_path, "x", round_number=1, persona="host", body="second")
    # --force 才允许覆盖
    write_round_comment(tmp_path, "x", round_number=1, persona="host", body="v2", overwrite=True)
    assert scan_plane(tmp_path).topics[0].comments[0].content.strip() == "v2"


def test_advance_updates_index_round(tmp_path: Path) -> None:
    write_topic_index(tmp_path, "adv", title="Adv", creator="host")
    write_round_comment(tmp_path, "adv", round_number=1, persona="host", body="# r1")
    update_topic_index(tmp_path, "adv", round="round2")
    topic = scan_plane(tmp_path).topics[0]
    assert topic.round == "round2"
    assert topic.round_number == 2


def test_derive_work_from_file_presence(tmp_path: Path) -> None:
    write_topic_index(tmp_path, "w", title="W", creator="host")
    write_round_comment(tmp_path, "w", round_number=1, persona="host", body="# r1 host")
    write_round_comment(tmp_path, "w", round_number=1, persona="participant", body="# r1 part")
    update_topic_index(tmp_path, "w", round="round2")
    write_round_comment(tmp_path, "w", round_number=2, persona="host", body="# r2 host")

    topic = scan_plane(tmp_path).topics[0]

    host_items = derive_work(topic, "host")
    assert any(i.kind == "round_ack_pending" and "participant" in i.detail for i in host_items)

    part_items = derive_work(topic, "participant")
    assert any(i.kind == "pending_topic_reply" and i.round == 2 for i in part_items)


# ---------------------------------------------------------------------------
# 参与人白名单（fs-participant-whitelist 实验）
# ---------------------------------------------------------------------------


def test_whitelist_blocks_non_participant_pending(tmp_path: Path) -> None:
    """白名单外 persona（reviewer）对 open 话题零待办。"""
    write_topic_index(tmp_path, "wl", title="WL", creator="host")
    write_round_comment(tmp_path, "wl", round_number=1, persona="host", body="# r1")

    topic = scan_plane(tmp_path).topics[0]
    assert derive_work(topic, "reviewer") == []
    # 未发言过的陌生 persona 同样无待办
    assert derive_work(topic, "outsider") == []


def test_declared_participants_generate_pending(tmp_path: Path) -> None:
    """front-matter 声明的参与人有待办，未声明者无。"""
    write_topic_index(
        tmp_path, "dec", title="Dec", creator="host", participants=["participant"]
    )
    write_round_comment(tmp_path, "dec", round_number=1, persona="host", body="# r1")

    topic = scan_plane(tmp_path).topics[0]
    assert topic.declared_participants == ["participant"]
    # participant 被 host 声明 → 有待办
    assert any(i.kind == "pending_topic_reply" for i in derive_work(topic, "participant"))
    # reviewer 未声明 → 无待办
    assert derive_work(topic, "reviewer") == []
    # host 的 ack 待办覆盖全量 participants（含 declared），不因白名单收缩
    host_items = derive_work(topic, "host")
    assert any(i.kind == "round_ack_pending" and "participant" in i.detail for i in host_items)


def test_comment_auto_merges_new_participant(tmp_path: Path) -> None:
    """发言即参与：新作者发言后自动并入 index.md participants，随后收到待办。"""
    write_topic_index(tmp_path, "auto", title="Auto", creator="host")
    write_round_comment(tmp_path, "auto", round_number=1, persona="host", body="# r1")

    topic = scan_plane(tmp_path).topics[0]
    assert "participant" not in topic.participants

    # participant 首次发言 → index.md 自动并入
    write_round_comment(tmp_path, "auto", round_number=1, persona="participant", body="# p1")
    topic = scan_plane(tmp_path).topics[0]
    assert "participant" in topic.participants
    assert "participant" in topic.declared_participants  # 持久化在 front-matter

    # 推进到 round2 后，participant（已在白名单）收到待办；reviewer 仍无
    update_topic_index(tmp_path, "auto", round="round2")
    topic = scan_plane(tmp_path).topics[0]
    assert any(i.kind == "pending_topic_reply" for i in derive_work(topic, "participant"))
    assert derive_work(topic, "reviewer") == []


def test_participants_frontmatter_scalar_fallback(tmp_path: Path) -> None:
    """手写 YAML 常见单值写法 participants: participant 按单元素处理。"""
    write_topic_index(tmp_path, "scalar", title="Scalar", creator="host")
    index = tmp_path / "map" / "topics" / "scalar" / "index.md"
    text = index.read_text(encoding="utf-8")
    text = text.replace("creator: host\n", "creator: host\nparticipants: participant\n")
    index.write_text(text, encoding="utf-8")

    topic = scan_plane(tmp_path).topics[0]
    assert topic.declared_participants == ["participant"]
    assert any(i.kind == "pending_topic_reply" for i in derive_work(topic, "participant"))


def test_legacy_topic_without_declared_participants(tmp_path: Path) -> None:
    """存量话题（无 participants front-matter）行为与改造前一致。"""
    write_topic_index(tmp_path, "legacy", title="Legacy", creator="host")
    write_round_comment(tmp_path, "legacy", round_number=1, persona="host", body="# r1")

    topic = scan_plane(tmp_path).topics[0]
    assert topic.declared_participants == []
    # 发言者 ∪ creator 仍在白名单内
    assert topic.participants == ["host"]
    assert derive_work(topic, "host") == []  # host 本轮已发言，无 ack 待办（无他人）
    assert derive_work(topic, "participant") == []  # 未发言未声明 → 无待办（改造前会误报）

    # participant 发言后成为参与人
    write_round_comment(tmp_path, "legacy", round_number=1, persona="participant", body="# p1")
    update_topic_index(tmp_path, "legacy", round="round2")
    topic = scan_plane(tmp_path).topics[0]
    assert topic.participants == ["host", "participant"]
    assert any(i.kind == "pending_topic_reply" for i in derive_work(topic, "participant"))


def test_overwrite_refreshes_posted_at(tmp_path: Path) -> None:
    """--force 覆盖写刷新 posted_at，消除审计歧义。"""
    write_topic_index(tmp_path, "ow", title="OW", creator="host")
    write_round_comment(tmp_path, "ow", round_number=1, persona="host", body="v1")
    first = scan_plane(tmp_path).topics[0].comments[0].posted_at

    write_round_comment(tmp_path, "ow", round_number=1, persona="host", body="v2", overwrite=True)
    second = scan_plane(tmp_path).topics[0].comments[0].posted_at
    assert second is not None and first is not None
    assert second >= first


# ---------------------------------------------------------------------------
# API：fs plane + 主读路径合并 + 验证型写
# ---------------------------------------------------------------------------


def _create_project(client, admin_headers: dict, workspace: Path) -> dict:
    response = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={
            "project_key": f"fs-{uuid.uuid4().hex[:8]}",
            "name": "FS Test Project",
            "workspace_path": str(workspace),
        },
    )
    assert response.status_code == 201
    return response.json()


def test_fs_topics_api_and_main_list_merge(client, admin_headers: dict, tmp_path: Path) -> None:
    project = _create_project(client, admin_headers, tmp_path)
    pid = project["id"]
    write_topic_index(tmp_path, "fs-api", title="FS API Topic", creator="admin-agent")
    write_round_comment(
        tmp_path, "fs-api", round_number=1, persona="admin-agent", body="# 立场\n支持"
    )

    # 专用 fs plane 端点（实时解析）
    response = client.get(f"/api/v1/projects/{pid}/fs/topics", headers=admin_headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["slug"] == "fs-api"
    assert body[0]["comment_count"] == 1

    # 主 /topics 列表合并（FS 并入）
    merged = client.get(f"/api/v1/projects/{pid}/topics", headers=admin_headers)
    assert merged.status_code == 200
    slugs = [t.get("slug") for t in merged.json()]
    assert "fs-api" in slugs

    # 主详情端点按确定性 id 命中 FS topic
    detail = client.get(
        f"/api/v1/topics/{topic_id_for_slug('fs-api')}", headers=admin_headers
    )
    assert detail.status_code == 200
    data = detail.json()
    assert data["slug"] == "fs-api"
    assert data["comments"][0]["author_name"] == "admin-agent"
    assert data["comments"][0]["excerpt"] == "立场"


def test_fs_advance_round_ack_validation(client, admin_headers: dict, tmp_path: Path) -> None:
    project = _create_project(client, admin_headers, tmp_path)
    pid = project["id"]
    write_topic_index(tmp_path, "fs-adv", title="Advance", creator="admin-agent")
    write_round_comment(tmp_path, "fs-adv", round_number=1, persona="admin-agent", body="# r1 host")
    write_round_comment(
        tmp_path, "fs-adv", round_number=1, persona="test-participant", body="# r1 part"
    )

    # round1 双方已发言 → 推进到 round2
    ok = client.post(
        f"/api/v1/projects/{pid}/fs/topics/fs-adv/advance-round", headers=admin_headers
    )
    assert ok.status_code == 200
    assert ok.json()["discussion_round"] == "round2"

    # round2 participant 未发言 → 409 + missing
    conflict = client.post(
        f"/api/v1/projects/{pid}/fs/topics/fs-adv/advance-round", headers=admin_headers
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["missing"] == ["test-participant"]

    # waive_ack → 推进到 round3，index.md 同步更新
    waived = client.post(
        f"/api/v1/projects/{pid}/fs/topics/fs-adv/advance-round",
        headers=admin_headers,
        json={"waive_ack": True, "waive_reason": "participant 离线"},
    )
    assert waived.status_code == 200
    assert waived.json()["discussion_round"] == "round3"
    topic = scan_plane(tmp_path).topic_by_slug("fs-adv")
    assert topic is not None and topic.round == "round3"


def test_fs_advance_round_forbidden_for_non_host(
    client, admin_headers: dict, tmp_path: Path
) -> None:
    project = _create_project(client, admin_headers, tmp_path)
    # 造一个该项目的普通 agent（非 creator、非 admin）
    agent_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={"name": "fs-bystander", "role": "agent", "project_key": project["project_key"]},
    )
    assert agent_resp.status_code == 201
    bystander_headers = {"Authorization": f"Bearer {agent_resp.json()['api_token']}"}

    write_topic_index(tmp_path, "fs-guard", title="Guard", creator="admin-agent")
    write_round_comment(tmp_path, "fs-guard", round_number=1, persona="admin-agent", body="# r1")

    response = client.post(
        f"/api/v1/projects/{project['id']}/fs/topics/fs-guard/advance-round",
        headers=bystander_headers,
        json={"waive_ack": True},
    )
    assert response.status_code == 403


def test_fs_write_allows_bootstrap_persona_agent(
    client, admin_headers: dict, tmp_path: Path
) -> None:
    """回归：bootstrap persona agent（name=multi-agent-platform-host，role=agent）
    操作 creator=host 的话题不应 403——creator 存短名，比对前须归一。"""
    project = _create_project(client, admin_headers, tmp_path)
    pid = project["id"]
    agent_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": "multi-agent-platform-host",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert agent_resp.status_code == 201
    host_headers = {"Authorization": f"Bearer {agent_resp.json()['api_token']}"}

    write_topic_index(tmp_path, "fs-persona", title="Persona", creator="host")
    write_round_comment(tmp_path, "fs-persona", round_number=1, persona="host", body="# r1")
    write_round_comment(
        tmp_path, "fs-persona", round_number=1, persona="participant", body="# r1"
    )

    ok = client.post(
        f"/api/v1/projects/{pid}/fs/topics/fs-persona/advance-round", headers=host_headers
    )
    assert ok.status_code == 200
    assert ok.json()["discussion_round"] == "round2"

    closed = client.post(
        f"/api/v1/projects/{pid}/fs/topics/fs-persona/close", headers=host_headers
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"


def test_fs_merge_pagination_stable_across_pages(
    client, admin_headers: dict, tmp_path: Path
) -> None:
    """回归：合并视图统一分页——FS 话题不逐页重复，X-Total-Count 为合并总数。"""
    project = _create_project(client, admin_headers, tmp_path)
    pid = project["id"]
    for i in range(3):
        resp = client.post(
            f"/api/v1/projects/{pid}/topics",
            headers=admin_headers,
            json={"title": f"DB Topic {i}", "slug": f"db-topic-{i}"},
        )
        assert resp.status_code == 201
    write_topic_index(tmp_path, "fs-page", title="FS Page Topic", creator="host")

    pages = []
    for page_no in (1, 2):
        resp = client.get(
            f"/api/v1/projects/{pid}/topics",
            headers=admin_headers,
            params={"page": page_no, "page_size": 2},
        )
        assert resp.status_code == 200
        assert resp.headers["X-Total-Count"] == "4"
        pages.append([t["slug"] for t in resp.json()])

    assert len(pages[0]) == 2 and len(pages[1]) == 2
    assert "fs-page" in pages[0]
    assert "fs-page" not in pages[1]
    assert not (set(pages[0]) & set(pages[1]))
    assert set(pages[0]) | set(pages[1]) == {
        "fs-page",
        "db-topic-0",
        "db-topic-1",
        "db-topic-2",
    }


def test_fs_merge_respects_creator_filter(
    client, admin_headers: dict, tmp_path: Path
) -> None:
    """回归：?creator_agent_id 过滤对 FS 话题同样生效。"""
    project = _create_project(client, admin_headers, tmp_path)
    pid = project["id"]
    agent_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": "fs-creator-a",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert agent_resp.status_code == 201
    creator_id = agent_resp.json()["id"]

    write_topic_index(tmp_path, "fs-by-a", title="By A", creator="fs-creator-a")
    write_topic_index(tmp_path, "fs-by-b", title="By B", creator="someone-else")

    resp = client.get(
        f"/api/v1/projects/{pid}/topics",
        headers=admin_headers,
        params={"creator_agent_id": creator_id},
    )
    assert resp.status_code == 200
    slugs = [t["slug"] for t in resp.json()]
    assert "fs-by-a" in slugs
    assert "fs-by-b" not in slugs


def test_fs_close_writes_index_and_conflicts_on_reclose(
    client, admin_headers: dict, tmp_path: Path
) -> None:
    project = _create_project(client, admin_headers, tmp_path)
    pid = project["id"]
    write_topic_index(tmp_path, "fs-close", title="Close", creator="admin-agent")
    write_round_comment(tmp_path, "fs-close", round_number=1, persona="admin-agent", body="# done")

    closed = client.post(
        f"/api/v1/projects/{pid}/fs/topics/fs-close/close",
        headers=admin_headers,
        json={"close_reason": "讨论完成", "close_note": "结论已沉淀"},
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"

    topic = scan_plane(tmp_path).topic_by_slug("fs-close")
    assert topic is not None and topic.status == "closed"

    again = client.post(
        f"/api/v1/projects/{pid}/fs/topics/fs-close/close", headers=admin_headers
    )
    assert again.status_code == 409


def test_fs_work_endpoint(client, admin_headers: dict, tmp_path: Path) -> None:
    project = _create_project(client, admin_headers, tmp_path)
    write_topic_index(tmp_path, "fs-work", title="Work", creator="host")
    write_round_comment(tmp_path, "fs-work", round_number=1, persona="host", body="# r1")
    write_round_comment(
        tmp_path, "fs-work", round_number=1, persona="participant", body="# r1"
    )
    update_topic_index(tmp_path, "fs-work", round="round2")

    response = client.get(
        f"/api/v1/projects/{project['id']}/fs/work",
        headers=admin_headers,
        params={"persona": "participant"},
    )
    assert response.status_code == 200
    items = response.json()
    assert any(i["kind"] == "pending_topic_reply" and i["round"] == 2 for i in items)


def test_fs_work_snapshot_wakes_and_clears_by_file_presence(
    client, admin_headers: dict, tmp_path: Path
) -> None:
    """GET /agents/me/work 并入 FS 待办；写文件即清除（waker 触发源同源）。"""
    project = _create_project(client, admin_headers, tmp_path)
    agent_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": "fs-worker",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert agent_resp.status_code == 201
    worker_headers = {"Authorization": f"Bearer {agent_resp.json()['api_token']}"}

    write_topic_index(tmp_path, "fs-wake", title="Wake", creator="fs-worker")
    write_round_comment(tmp_path, "fs-wake", round_number=1, persona="fs-worker", body="# r1")
    write_round_comment(tmp_path, "fs-wake", round_number=1, persona="alice", body="# r1")
    update_topic_index(tmp_path, "fs-wake", round="round2")

    def _fs_items(headers: dict) -> list[dict]:
        response = client.get("/api/v1/agents/me/work", headers=headers)
        assert response.status_code == 200
        return [
            item
            for item in response.json()["topic_progress"]["items"]
            if item["topic_id"] == str(topic_id_for_slug("fs-wake"))
        ]

    entries = _fs_items(worker_headers)
    assert entries, "FS topic 待办应并入 agents/me/work 快照"
    kinds = [w["kind"] for w in entries[0]["work_items"]]
    assert "pending_topic_reply" in kinds

    # 写 round2 发言文件 → pending_topic_reply 即刻清除
    write_round_comment(tmp_path, "fs-wake", round_number=2, persona="fs-worker", body="# r2")
    entries = _fs_items(worker_headers)
    kinds = [w["kind"] for e in entries for w in e["work_items"]]
    assert "pending_topic_reply" not in kinds


def test_empty_workspace_yields_empty_plane(client, admin_headers: dict, tmp_path: Path) -> None:
    project = _create_project(client, admin_headers, tmp_path)
    response = client.get(f"/api/v1/projects/{project['id']}/fs/topics", headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == []
