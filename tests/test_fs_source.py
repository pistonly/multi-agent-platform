"""fs plane：map/ 文件夹事实源（解析器 + API 合并 + 验证型写 + work 推导）。"""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone
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
from sqlalchemy import select

from server.domain.models import Agent, Project, Topic, TopicStatus
from tests._frontmatter import make_valid_plan

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


def test_write_rejects_empty_slug(tmp_path: Path) -> None:
    """空 slug 防护：None/空白 slug 抛可读 ValueError，而非路径拼接 TypeError。

    背景：typer 0.16.1 + click 8.4.x 组合不强制校验必填 CLI 选项，缺失的
    ``--slug`` 会以 None 穿透到 SDK 写函数（回归见 test_topic_routing.py）。"""
    with pytest.raises(ValueError, match="non-empty folder name"):
        write_topic_index(tmp_path, "", title="X", creator="host")
    with pytest.raises(ValueError, match="non-empty folder name"):
        write_topic_index(tmp_path, None, title="X", creator="host")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-empty folder name"):
        write_round_comment(tmp_path, "", round_number=1, persona="host", body="x")


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
    client, admin_headers: dict, tmp_path: Path, db_session
) -> None:
    """回归：合并视图统一分页——FS 话题不逐页重复，X-Total-Count 为合并总数。

    M58 后 DB 话题写端点已退役（410），DB 话题改经 ORM 直插播种，
    维持原本「DB + FS 合并分页稳定性」的验证意图。
    """
    project = _create_project(client, admin_headers, tmp_path)
    pid = project["id"]
    db_project = db_session.get(Project, uuid.UUID(pid))
    admin = db_session.scalar(select(Agent).where(Agent.role == "admin"))
    for i in range(3):
        db_session.add(
            Topic(
                project_id=db_project.id,
                creator_agent_id=admin.id,
                title=f"DB Topic {i}",
                slug=f"db-topic-{i}",
                description="pagination seed",
                status=TopicStatus.open,
            )
        )
    db_session.commit()
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
        json={"close_reason": "discussion_converged", "close_note": "结论已沉淀"},
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


def test_fs_stale_open_topics_nudge(
    client, admin_headers: dict, tmp_path: Path
) -> None:
    """ready/久未推进的开放话题 → creator(host) 得到 stale_open_topics 义务；
    挂活跃实验或 close 后消失；非 creator 不可见。"""
    project = _create_project(client, admin_headers, tmp_path)
    pid = project["id"]

    def _make_agent(name: str) -> dict:
        resp = client.post(
            "/api/v1/agents",
            headers=admin_headers,
            json={"name": name, "role": "agent", "project_key": project["project_key"]},
        )
        assert resp.status_code == 201
        return {"Authorization": f"Bearer {resp.json()['api_token']}"}

    # 名含「-host」→ persona 自动推导为 host（FS creator 必须匹配 persona 短名）
    host_headers = _make_agent("fs-host")
    other_headers = _make_agent("fs-other")

    def _backdate_created(slug: str, hours: int = 3) -> None:
        # FS 话题的 updated_at 时钟 = frontmatter created_at（或最晚评论），
        # 不是 index mtime——回拨 created_at 才能让它超过 stale 阈值。
        index_path = tmp_path / "map" / "topics" / slug / "index.md"
        old = time.time() - hours * 3600
        iso = datetime.fromtimestamp(old, timezone.utc).isoformat()
        text = re.sub(
            r"^created_at: .*$", f"created_at: '{iso}'", index_path.read_text(encoding="utf-8"), flags=re.M
        )
        index_path.write_text(text, encoding="utf-8")

    def _stale_kinds(headers: dict, slug: str) -> list[str]:
        response = client.get("/api/v1/agents/me/work", headers=headers)
        assert response.status_code == 200
        return [
            w["kind"]
            for item in response.json()["topic_progress"]["items"]
            if item["topic_id"] == str(topic_id_for_slug(slug))
            for w in item["work_items"]
        ]

    # 话题 A：ready + 久未推进 → creator 见 stale nudge；非 creator 不可见
    write_topic_index(
        tmp_path, "stale-close", title="Stale Close", creator="host", round_="ready"
    )
    _backdate_created("stale-close")
    assert "stale_open_topics" in _stale_kinds(host_headers, "stale-close")
    assert "stale_open_topics" not in _stale_kinds(other_headers, "stale-close")

    # close 落结论 → nudge 消失（清理动作 = topic close）
    resp = client.post(
        f"/api/v1/projects/{pid}/fs/topics/stale-close/close",
        headers=host_headers,
        json={"close_reason": "experiment_done", "close_note": "实验已完成并验收"},
    )
    assert resp.status_code in (200, 201)
    assert "stale_open_topics" not in _stale_kinds(host_headers, "stale-close")

    # 话题 B：挂活跃实验（draft）→ 排除，避免 close 被锁时仍被催
    write_topic_index(
        tmp_path, "stale-exp", title="Stale Exp", creator="host", round_="ready"
    )
    _backdate_created("stale-exp")
    assert "stale_open_topics" in _stale_kinds(host_headers, "stale-exp")
    created = client.post(
        f"/api/v1/projects/{pid}/experiments",
        headers=admin_headers,
        json={
            "title": "blocking experiment",
            "topic_id": str(topic_id_for_slug("stale-exp")),
            "plan": {"content_md": make_valid_plan(body="## p")},
        },
    )
    assert created.status_code in (200, 201)
    assert "stale_open_topics" not in _stale_kinds(host_headers, "stale-exp")


def test_empty_workspace_yields_empty_plane(client, admin_headers: dict, tmp_path: Path) -> None:
    project = _create_project(client, admin_headers, tmp_path)
    response = client.get(f"/api/v1/projects/{project['id']}/fs/topics", headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# M58 回归：experiment create 的 topic_id 三态路由（DB uuid / FS uuid5）
# ---------------------------------------------------------------------------


def test_experiment_create_on_fs_topic(
    client, admin_headers: dict, tmp_path: Path
) -> None:
    """FS 话题（uuid5 id，无 DB 行）可挂实验；同话题活跃实验仍互斥；未知 id 404。"""
    project = _create_project(client, admin_headers, tmp_path)
    pid = project["id"]
    write_topic_index(tmp_path, "fs-exp", title="FS Experiment Topic", creator="admin-agent")
    fs_topic_id = topic_id_for_slug("fs-exp")

    response = client.post(
        f"/api/v1/projects/{pid}/experiments",
        headers=admin_headers,
        json={
            "title": "M58 fs-topic experiment",
            "topic_id": str(fs_topic_id),
            "plan": {"content_md": make_valid_plan(body="## fs plan")},
        },
    )
    assert response.status_code in (200, 201)
    body = response.json()
    assert body["topic_id"] == str(fs_topic_id)

    # 同一 FS 话题上的第二个活跃实验 → 409
    dup = client.post(
        f"/api/v1/projects/{pid}/experiments",
        headers=admin_headers,
        json={
            "title": "dup on same fs topic",
            "topic_id": str(fs_topic_id),
            "plan": {"content_md": make_valid_plan(body="## fs plan 2")},
        },
    )
    assert dup.status_code == 409

    # 未知 uuid（既非 DB 也非 FS）→ 404
    ghost = client.post(
        f"/api/v1/projects/{pid}/experiments",
        headers=admin_headers,
        json={
            "title": "ghost topic",
            "topic_id": str(uuid.uuid4()),
            "plan": {"content_md": make_valid_plan(body="## ghost")},
        },
    )
    assert ghost.status_code == 404


# ---------------------------------------------------------------------------
# advance-round ack 合规校验（experiment 4b1192cc：D1/D2/D3/D4）
# ---------------------------------------------------------------------------


def _handwrite(d: Path, filename: str, text: str) -> None:
    """直接手写 round 文件（不经 write_round_comment，模拟旁路）。"""
    (d / filename).write_text(text, encoding="utf-8")


def test_handwritten_round_file_excluded_from_effective_ack(tmp_path: Path) -> None:
    """D1/D2：手写旁路文件（无 frontmatter）ack_valid=False，
    effective ack authors 不含该 persona。"""
    write_topic_index(
        tmp_path, "hw", title="HW", creator="host", participants=["participant"]
    )
    write_round_comment(tmp_path, "hw", round_number=1, persona="host", body="# r1 host")
    _handwrite(
        tmp_path / "map" / "topics" / "hw",
        "round1-participant.md",
        "# 手写正文（无 frontmatter）\n",
    )

    topic = scan_plane(tmp_path).topics[0]
    part_file = next(c for c in topic.comments if c.file_persona == "participant")
    assert part_file.ack_valid is False
    assert part_file.ack_error == "frontmatter author missing"
    # effective ack authors 只认 host（手写旁路被拒）
    assert topic.authors_in_round(1) == {"host"}


def test_frontmatter_field_semantics_ack_reasons(tmp_path: Path) -> None:
    """D2 三条：author 与文件名 persona 不符 / round 与文件名轮次不符 /
    posted_at 缺失，各自进 ack_error，具体可验。"""
    d = tmp_path / "map" / "topics" / "fm"
    write_topic_index(tmp_path, "fm", title="FM", creator="host", participants=["participant"])
    good = "---\nauthor: participant\nround: 1\nposted_at: '2026-08-24T00:00:00+00:00'\n---\n# ok\n"
    _handwrite(d, "round1-participant.md", good)
    _handwrite(
        d,
        "round1-reviewer.md",
        "---\nauthor: host\nround: 1\nposted_at: '2026-08-24T00:00:00+00:00'\n---\n# 替人表态\n",
    )
    _handwrite(
        d,
        "round2-participant.md",
        "---\nauthor: participant\nround: 1\nposted_at: '2026-08-24T00:00:00+00:00'\n---\n# 旧轮挪位\n",
    )
    _handwrite(
        d,
        "round1-host.md",
        "---\nauthor: host\nround: 1\n---\n# 无 posted_at 空壳\n",
    )

    topic = scan_plane(tmp_path).topics[0]

    def _by_file(suffix: str):
        return next(c for c in topic.comments if c.file_path.endswith(suffix))

    # 合规文件
    assert _by_file("round1-participant.md").ack_valid is True
    # author 与文件名 persona 不符
    assert _by_file("round1-reviewer.md").ack_error == (
        "frontmatter author=host, expected reviewer"
    )
    # round2-participant.md round=1 与文件名轮次 2 不符
    assert _by_file("round2-participant.md").ack_error == "round=1, expected 2"
    # frontmatter 无 posted_at → 空壳被拦截
    assert _by_file("round1-host.md").ack_valid is False
    assert _by_file("round1-host.md").ack_error == "posted_at missing or unparseable"


def test_ack_participant_scope_and_stray_report(tmp_path: Path) -> None:
    """D3：ack 名单=creator∪declared；名单外 reviewer 的合规文件进 stray
    报告、挂 ack 满员判定时不扩员。"""
    write_topic_index(
        tmp_path, "scope", title="S", creator="host", participants=["participant"]
    )
    write_round_comment(tmp_path, "scope", round_number=1, persona="host", body="# r1")
    write_round_comment(tmp_path, "scope", round_number=1, persona="participant", body="# r1p")
    # reviewer 手写**合规** frontmatter 文件（D2 三字段齐全）但未被声明
    _handwrite(
        tmp_path / "map" / "topics" / "scope",
        "round1-reviewer.md",
        "---\nauthor: reviewer\nround: 1\nposted_at: '2026-08-24T00:00:00+00:00'\n---\n# r1r\n",
    )

    topic = scan_plane(tmp_path).topics[0]
    assert topic.ack_participants() == ["host", "participant"]
    # 名单内人员本轮均已合规发言 → host 无 ack 待办（reviewer 不阻塞）
    assert not any(i.kind == "round_ack_pending" for i in derive_work(topic, "host"))
    # 名单外文件单独 anomaly 报告
    stray = topic.stray_files_in_round(1)
    assert [c.file_persona for c in stray] == ["reviewer"]


def test_derive_work_missing_includes_reason(tmp_path: Path) -> None:
    """A5：host work 的 round_ack_pending detail 对不合规文件逐条指认
    文件名 + 原因（与 advance 409 missing_reasons 同口径）。"""
    write_topic_index(
        tmp_path, "wr", title="W", creator="host", participants=["participant"]
    )
    write_round_comment(tmp_path, "wr", round_number=1, persona="host", body="# r1")
    _handwrite(
        tmp_path / "map" / "topics" / "wr",
        "round1-participant.md",
        "# 手写（无 frontmatter）\n",
    )

    topic = scan_plane(tmp_path).topics[0]
    host_items = derive_work(topic, "host")
    pending = next(i for i in host_items if i.kind == "round_ack_pending")
    assert "round1-participant.md" in pending.detail
    assert "frontmatter author missing" in pending.detail


def test_work_and_advance_same_origin_reject_handwritten(
    client, admin_headers: dict, tmp_path: Path
) -> None:
    """A1 双界面同源：同一手写旁路文件在 host work round_ack_pending 与
    advance-round 409 missing_reasons 中一致被拒、口径相同。"""
    project = _create_project(client, admin_headers, tmp_path)
    pid = project["id"]
    # participant 在 declared 名单（被 host 声明），但本轮手写旁路无 frontmatter
    write_topic_index(
        tmp_path, "fs-hw", title="HW", creator="admin-agent", participants=["test-participant"]
    )
    write_round_comment(tmp_path, "fs-hw", round_number=1, persona="admin-agent", body="# r1")
    _handwrite(
        tmp_path / "map" / "topics" / "fs-hw",
        "round1-test-participant.md",
        "# 手写旁路\n",
    )

    topic = scan_plane(tmp_path).topics[0]
    # host 视角 work：hand-write 文件列为未发言并带原因
    admin_items = derive_work(topic, "admin-agent")
    pending = next(i for i in admin_items if i.kind == "round_ack_pending")
    assert "round1-test-participant.md" in pending.detail

    # advance 门槛：同样拒绝，missing + missing_reasons 逐条指认
    resp = client.post(
        f"/api/v1/projects/{pid}/fs/topics/fs-hw/advance-round", headers=admin_headers
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "round_ack_pending"
    assert "test-participant" in detail["missing"]
    reasons = detail["missing_reasons"]
    assert "round1-test-participant.md" in reasons["test-participant"]
    assert "frontmatter author missing" in reasons["test-participant"]


def test_fs_unread_change_handoff(
    client, admin_headers: dict, tmp_path: Path
) -> None:
    """对方发言 → 白名单内 persona 得到 unread_change contextual 项（交接信号）；
    自己是最新发言者时消失；白名单外（reviewer）始终不可见。"""
    project = _create_project(client, admin_headers, tmp_path)

    def _make_agent(name: str) -> dict:
        resp = client.post(
            "/api/v1/agents",
            headers=admin_headers,
            json={"name": name, "role": "agent", "project_key": project["project_key"]},
        )
        assert resp.status_code == 201
        return {"Authorization": f"Bearer {resp.json()['api_token']}"}

    host_headers = _make_agent("fs-host")
    participant_headers = _make_agent("fs-participant")
    reviewer_headers = _make_agent("fs-reviewer")

    def _kinds(headers: dict, slug: str) -> list[str]:
        response = client.get("/api/v1/agents/me/work", headers=headers)
        assert response.status_code == 200
        return [
            w["kind"]
            for item in response.json()["topic_progress"]["items"]
            if item["topic_id"] == str(topic_id_for_slug(slug))
            for w in item["work_items"]
        ]

    write_topic_index(
        tmp_path,
        "handoff",
        title="Handoff",
        creator="host",
        participants=["host", "participant"],
    )
    write_round_comment(tmp_path, "handoff", round_number=1, persona="host", body="# 开场\nhost 先说")

    # host 是最新发言者 → host 无 unread_change；participant（白名单内，
    # 最后发言者是别人）有 unread_change + pending_topic_reply
    assert "unread_change" not in _kinds(host_headers, "handoff")
    participant_kinds = _kinds(participant_headers, "handoff")
    assert "unread_change" in participant_kinds
    assert "pending_topic_reply" in participant_kinds
    # reviewer 不在白名单 → 任何时刻都看不到
    assert "unread_change" not in _kinds(reviewer_headers, "handoff")

    # participant 发言（交接）→ host 立即得到 unread_change，无需等 stale
    write_round_comment(tmp_path, "handoff", round_number=1, persona="participant", body="# 回应\nparticipant 意见")
    assert "unread_change" in _kinds(host_headers, "handoff")
    # participant 自己是最新发言者 → 自己的 unread_change 消失（仅剩义务项）
    assert "unread_change" not in _kinds(participant_headers, "handoff")
    assert "unread_change" not in _kinds(reviewer_headers, "handoff")

    # host 再发言 → 交接回 participant
    write_round_comment(tmp_path, "handoff", round_number=2, persona="host", body="# Summary\nhost 总结")
    assert "unread_change" not in _kinds(host_headers, "handoff")
    assert "unread_change" in _kinds(participant_headers, "handoff")
