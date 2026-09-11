"""`topic_db_read_retired` flag：内容侧 DB 读路径退役门禁（实验 0f271f7e A5）。

契约：

- **flag OFF / 未 set（默认）**：读路径与 v0.13 M58 逐字节一致 —— DB
  fallback 全部保留（存量行为的回归由 tests/test_topics.py 兜底）。
- **flag ON**：list 只回 FS 段（含空集，不再触 SQL 分页 / DB total）；
  detail / comments 在 FS miss 且 DB 行存在（未迁移存量）时 410 引导
  `map topic migrate`；DB 行不存在（FS-native）→ 原 404 语义；
  PATCH archived 410 引导 `map topic archive`。
- **评论 FS 化**：flag ON 时 comments 唯一来源是 FS 视图
  （`fs_topic_comments_as_reads`：comment_seq 升序、无线程、author 名字直透）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from map_types.enums import TopicStatus
from sqlalchemy import select

from server.domain.models import Agent, Project, Topic
from server.services import feature_flag_service as flag_svc
from server.services import fs_source_service as fs_svc
from server.services.fs_topic_view import _CommentView, _TopicView
from tests._db_topic_factory import db_create_topic

pytestmark = pytest.mark.slow


def _test_agent_id(db_session) -> uuid.UUID:
    """test-agent（conftest agent_token 的 agent）作 topic creator。"""
    return db_session.scalar(select(Agent).where(Agent.name == "test-agent")).id


def _make_admin(db_session):
    from server.domain.models import Agent, AgentRole

    agent = Agent(
        id=uuid.uuid4(),
        name=f"flag-admin-{uuid.uuid4().hex[:8]}",
        api_token_hash="test-token-hash",
        api_token_prefix="tt",
        api_token_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        role=AgentRole.admin,
        project_id=None,
    )
    db_session.add(agent)
    db_session.flush()
    return agent


def _flip_on(db_session, project: dict) -> None:
    flag_svc.set_flag(
        db_session,
        project_id=uuid.UUID(project["id"]),
        flag_key=flag_svc.FLAG_TOPIC_DB_READ_RETIRED,
        flag_value="on",
        actor=_make_admin(db_session),
        reason="test flip: retire DB read path",
    )


def _fs_view(slug: str = "fs-native") -> _TopicView:
    now = datetime.now(timezone.utc)
    comments = [
        _CommentView(
            id=uuid.uuid5(uuid.NAMESPACE_URL, f"{slug}-c{seq}"),
            round=1,
            author="participant" if seq % 2 else "host",
            kind="user",
            is_round_summary=False,
            excerpt=f"excerpt-{seq}",
            content=f"body-{seq}",
            file_path=f"map/topics/{slug}/round1-x-{seq}.md",
            posted_at=now,
            comment_seq=seq,
        )
        for seq in (1, 2)
    ]
    return _TopicView(
        slug=slug,
        id=uuid.uuid5(uuid.NAMESPACE_URL, slug),
        title=f"FS topic {slug}",
        description="fs view fixture",
        status="open",
        round="round1",
        round_number=1,
        creator="host",
        created_at=now,
        updated_at=now,
        dir_path=f"map/topics/{slug}",
        comments=comments,
    )


# ---------------------------------------------------------------------------
# flag OFF / 未 set：行为不变
# ---------------------------------------------------------------------------


def test_flag_off_read_paths_unchanged(
    client, db_session, auth_headers, project
):
    """flag 显式 off：legacy topic 的 list / detail / comments / PATCH archived
    全部保持 M58 行为（DB fallback 不退役）。list 断言「DB 段仍在合并视图
    里」（FS 段内容取本机 workspace 实况，不对总量做环境敏感假设）。"""
    flag_svc.set_flag(
        db_session,
        project_id=uuid.UUID(project["id"]),
        flag_key=flag_svc.FLAG_TOPIC_DB_READ_RETIRED,
        flag_value="off",
        actor=_make_admin(db_session),
        reason="explicit off",
    )
    topic = db_create_topic(
        db_session,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=_test_agent_id(db_session),
    )

    listing = client.get(
        f"/api/v1/projects/{project['id']}/topics", headers=auth_headers
    )
    assert listing.status_code == 200
    assert str(topic.id) in {t["id"] for t in listing.json()}

    detail = client.get(f"/api/v1/topics/{topic.id}", headers=auth_headers)
    assert detail.status_code == 200

    comments = client.get(f"/api/v1/topics/{topic.id}/comments", headers=auth_headers)
    assert comments.status_code == 200

    archived = client.patch(
        f"/api/v1/topics/{topic.id}", headers=auth_headers, json={"archived": True}
    )
    assert archived.status_code == 200


# ---------------------------------------------------------------------------
# flag ON：list 只回 FS 段
# ---------------------------------------------------------------------------


def test_flag_on_list_topics_fs_only(client, db_session, auth_headers, project):
    """flag ON：DB 段整段退役 —— DB-only topic 从列表消失，列表与纯 FS 段
    一致（对 FS 段内容不做环境敏感假设）。"""
    topic = db_create_topic(
        db_session,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=_test_agent_id(db_session),
    )
    _flip_on(db_session, project)

    proj = db_session.get(Project, uuid.UUID(project["id"]))
    fs_slugs = {
        t.slug for t in fs_svc.fs_topics_as_summaries(db_session, proj) if t.slug
    }

    listing = client.get(
        f"/api/v1/projects/{project['id']}/topics", headers=auth_headers
    )
    assert listing.status_code == 200
    rows = listing.json()
    assert {t["slug"] for t in rows if t.get("slug")} == fs_slugs
    assert topic.slug not in {t["slug"] for t in rows}
    assert listing.headers["X-Total-Count"] == str(len(fs_slugs))


# ---------------------------------------------------------------------------
# flag ON：detail / comments —— FS miss + DB 行存在 → 410
# ---------------------------------------------------------------------------


def test_flag_on_detail_410_for_unmigrated_legacy_row(
    client, db_session, auth_headers, project
):
    topic = db_create_topic(
        db_session,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=_test_agent_id(db_session),
    )
    _flip_on(db_session, project)

    resp = client.get(f"/api/v1/topics/{topic.id}", headers=auth_headers)
    assert resp.status_code == 410
    detail = resp.json()["detail"]
    assert detail["error"] == "topic_db_read_retired"
    assert "map topic migrate" in detail["hint"]


def test_flag_on_detail_404_preserved_without_db_row(
    client, db_session, auth_headers, project
):
    """FS-native uuid（无 DB 行、无 FS 命中）→ 原 404 语义不变。"""
    _flip_on(db_session, project)
    resp = client.get(
        f"/api/v1/topics/{uuid.uuid4()}", headers=auth_headers
    )
    assert resp.status_code == 404


def test_flag_on_comments_410_for_unmigrated_legacy_row(
    client, db_session, auth_headers, project
):
    topic = db_create_topic(
        db_session,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=_test_agent_id(db_session),
    )
    _flip_on(db_session, project)

    resp = client.get(f"/api/v1/topics/{topic.id}/comments", headers=auth_headers)
    assert resp.status_code == 410
    assert resp.json()["detail"]["error"] == "topic_db_read_retired"


def test_flag_on_patch_archived_410(client, db_session, auth_headers, project):
    """归档即 FS 目录搬移：flag ON 后 PATCH archived 一并 410。"""
    topic = db_create_topic(
        db_session,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=_test_agent_id(db_session),
    )
    _flip_on(db_session, project)

    resp = client.patch(
        f"/api/v1/topics/{topic.id}", headers=auth_headers, json={"archived": True}
    )
    assert resp.status_code == 410
    detail = resp.json()["detail"]
    assert detail["error"] == "topic_write_retired"
    assert "map topic archive" in detail["hint"]


# ---------------------------------------------------------------------------
# flag ON：comments FS 化
# ---------------------------------------------------------------------------


def test_flag_on_comments_served_from_fs_view(
    client, db_session, auth_headers, project, monkeypatch
):
    """FS 命中（uuid 相同）→ 评论来自 FS 视图：seq 升序、author 名字直透。
    flag gate 以 DB 行存在为前提 —— 直接以 view.id 落一行 legacy DB topic
    （模拟 `map topic migrate` 之前的 uuid 恒等关系）。"""
    view = _fs_view()
    db_session.add(
        Topic(
            id=view.id,
            project_id=uuid.UUID(project["id"]),
            creator_agent_id=_test_agent_id(db_session),
            title=view.title,
            description="legacy row sharing the FS uuid5",
            slug=f"legacy-{view.slug}",
            status=TopicStatus.open,
            discussion_round="round1",
        )
    )
    db_session.flush()
    fs_project = SimpleNamespace(id=uuid.UUID(project["id"]))
    monkeypatch.setattr(
        fs_svc, "find_fs_topic_by_id", lambda db, tid: (fs_project, view)
    )
    _flip_on(db_session, project)

    resp = client.get(f"/api/v1/topics/{view.id}/comments", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["comment_seq"] for r in rows] == [1, 2]
    assert [r["author_name"] for r in rows] == ["participant", "host"]
    assert rows[0]["body"] == "body-1"
    assert all(r["parent_comment_id"] is None for r in rows)

    tree = client.get(
        f"/api/v1/topics/{view.id}/comments", headers=auth_headers, params={"tree": True}
    )
    assert tree.status_code == 200
    nodes = tree.json()
    assert [n["comment_seq"] for n in nodes] == [1, 2]
    assert all(n["children"] == [] for n in nodes)

    limited = client.get(
        f"/api/v1/topics/{view.id}/comments", headers=auth_headers, params={"limit": 1}
    )
    assert [r["comment_seq"] for r in limited.json()] == [1]
