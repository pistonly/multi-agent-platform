"""topic-progress 双源一致性：DB work items + FS（map/ 目录）投影。

回归点（v0.19）：此前 FS 投影只被 ``GET /agents/me/work`` 合并，
``GET /agents/me/topic-progress``（``map topic progress``）只看 DB —— 本机
local plane 的话题全是 FS 话题，于是该端点恒返回 ``items: []``，而同一时刻
``map work`` 显示这些话题有 obligation 待办。修复把合并收敛进
``topic_progress_service.list_topic_progress_for_agent``，本文件锁住四点：

1. 服务层默认同时含 DB 与 FS 两个来源（端点不再漏）
2. ``include_fs=False`` 只剩 DB —— 即修复前 topic-progress 端点的行为，作对照
3. ``get_agent_work`` 与 topic-progress 结果一致且**不重复计数**（外层手工合并已删）
4. 按 ``topic_id`` 去重（远程模式投影缓存行可能与 DB 行指向同一话题）
"""

from __future__ import annotations

import uuid
from pathlib import Path

from map_fs import topic_id_for_slug, write_round_comment, write_topic_index
from map_types.enums import AgentRole
from sqlalchemy.orm import Session

from server.domain.models import Agent, Project
from server.domain.schemas import TopicProgressItemRead
from server.services import agent_work_service, topic_progress_service
from server.services.topic_progress_service import _with_fs_items

SLUG = "fs-merge"


def _seed_participant(db_session: Session, workspace: Path) -> Agent:
    """建一个 workspace 指向 tmp_path 的 project + participant persona agent。

    ``Agent.persona`` 由 name 尾段派生，故 name 必须以 ``-participant`` 结尾，
    否则 FS 投影的白名单匹配不到该 persona（participants 里存的是短名）。
    """
    project = Project(
        id=uuid.uuid4(),
        project_key=f"fsm-{uuid.uuid4().hex[:8]}",
        name="fs merge test",
        workspace_path=str(workspace),
        content_root="map",
    )
    db_session.add(project)
    db_session.flush()
    agent = Agent(
        id=uuid.uuid4(),
        name=f"fsm-{uuid.uuid4().hex[:6]}-participant",
        api_token_hash="h",
        api_token_prefix="tt",
        api_token_sha256=uuid.uuid4().hex,
        role=AgentRole.agent,
        project_id=project.id,
    )
    db_session.add(agent)
    db_session.flush()
    return agent


def _write_open_topic(workspace: Path, slug: str = SLUG) -> None:
    """open 话题 + host 开场，**故意不写** round1-participant.md。

    participant 在白名单内且本轮无自己的发言文件 → FS 投影给出
    ``pending_topic_reply``（obligation）。DB 侧查不到任何东西。
    """
    write_topic_index(
        workspace,
        slug,
        title="FS Merge",
        creator="host",
        participants=["host", "participant"],
    )
    write_round_comment(workspace, slug, round_number=1, persona="host", body="# host 开场")


# ---------------------------------------------------------------------------
# 1 + 2：服务层双源 / DB-only 对照
# ---------------------------------------------------------------------------


def test_topic_progress_includes_fs_projection(db_session: Session, tmp_path: Path) -> None:
    agent = _seed_participant(db_session, tmp_path)
    _write_open_topic(tmp_path)

    merged = topic_progress_service.list_topic_progress_for_agent(db_session, agent)
    assert [i.topic_id for i in merged.items] == [topic_id_for_slug(SLUG)]
    assert merged.total == 1
    # participant 缺本轮发言文件 → pending_topic_reply；最后发言者是 host
    # → 另有 contextual 的 unread_change（交接信号，同一 item 内并存）
    kinds = [w.kind for i in merged.items for w in i.work_items]
    assert "pending_topic_reply" in kinds


def test_include_fs_false_is_db_only(db_session: Session, tmp_path: Path) -> None:
    """``include_fs=False`` = 修复前 /me/topic-progress 的行为（FS 话题全丢）。"""
    agent = _seed_participant(db_session, tmp_path)
    _write_open_topic(tmp_path)

    db_only = topic_progress_service.list_topic_progress_for_agent(
        db_session, agent, include_fs=False
    )
    assert db_only.items == []
    assert db_only.total == 0


# ---------------------------------------------------------------------------
# 3：work 快照与 topic-progress 同源，且不重复计数
# ---------------------------------------------------------------------------


def test_work_snapshot_matches_topic_progress(db_session: Session, tmp_path: Path) -> None:
    agent = _seed_participant(db_session, tmp_path)
    _write_open_topic(tmp_path)

    work = agent_work_service.get_agent_work(db_session, agent)
    direct = topic_progress_service.list_topic_progress_for_agent(db_session, agent)

    assert [i.topic_id for i in work.topic_progress.items] == [
        i.topic_id for i in direct.items
    ]
    # 外层手工合并已删除 —— 保留会变成同一话题出现两次
    assert work.topic_progress.total == 1
    assert len(work.topic_progress.items) == 1


# ---------------------------------------------------------------------------
# 4：按 topic_id 去重（远程模式投影缓存可能与 DB 行重叠）
# ---------------------------------------------------------------------------


def test_merge_dedupes_by_topic_id(
    db_session: Session, tmp_path: Path, monkeypatch: object
) -> None:
    agent = _seed_participant(db_session, tmp_path)
    dup_id = topic_id_for_slug(SLUG)
    other_id = uuid.uuid4()
    db_item = TopicProgressItemRead(
        topic_id=dup_id, topic_title="dup", discussion_round="round1"
    )
    fs_dup = TopicProgressItemRead(
        topic_id=dup_id, topic_title="dup", discussion_round="round1"
    )
    fs_new = TopicProgressItemRead(
        topic_id=other_id, topic_title="new", discussion_round="round1"
    )

    from server.services import fs_source_service

    monkeypatch.setattr(  # type: ignore[attr-defined]
        fs_source_service, "fs_topic_progress_for_agent", lambda db, agent: [fs_dup, fs_new]
    )
    merged = _with_fs_items(db_session, agent, [db_item])

    assert [i.topic_id for i in merged] == [dup_id, other_id]
    assert merged[0] is db_item  # 同 topic_id 保留 DB 结果（信息更全）


# ---------------------------------------------------------------------------
# 端点层：map topic progress 现在能看到 FS 话题（用户实际踩到的那一面）
# ---------------------------------------------------------------------------


def test_topic_progress_endpoint_includes_fs_topic(client, admin_headers, tmp_path: Path) -> None:
    project_key = f"fsep-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={
            "project_key": project_key,
            "name": "FS endpoint test",
            "workspace_path": str(tmp_path),
        },
    )
    assert resp.status_code == 201
    _write_open_topic(tmp_path, slug="fs-endpoint")

    created = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={"name": "fs-endpoint-participant", "role": "agent", "project_key": project_key},
    )
    assert created.status_code == 201
    headers = {"Authorization": f"Bearer {created.json()['api_token']}"}

    progress = client.get("/api/v1/agents/me/topic-progress", headers=headers)
    assert progress.status_code == 200
    ids = [item["topic_id"] for item in progress.json()["items"]]
    assert str(topic_id_for_slug("fs-endpoint")) in ids

    # 与 work 快照同一个口径（此前 work 有、topic-progress 没有）
    work = client.get("/api/v1/agents/me/work", headers=headers)
    assert work.status_code == 200
    assert [item["topic_id"] for item in work.json()["topic_progress"]["items"]] == ids
