"""优化任务 T11 的回归测试（见 docs/OPTIMIZATION-TASKS.md）。

notifications 索引布局调整（migration 052）：

1. 新增复合索引 ``ix_notifications_recipient_updated(recipient_agent_id,
   updated_at)``——服务 ``list_for_agent`` 的
   ``WHERE recipient=? ORDER BY updated_at DESC`` 排序分页热点。
2. 裁剪 5 个冗余单列索引（fingerprint_version / category / read_at /
   group_key / recipient_agent_id），created_at 保留。

守卫：迁移 up/down 往返、ORM ``create_all`` 与迁移库索引集一致
（防 schema 漂移）、SQLite 查询计划确实走复合索引。
"""

from __future__ import annotations

import os

from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from alembic import command
from server.config import get_settings
from server.domain.models import AgentRole, Notification, Project
from server.services.auth import create_agent

# migration 052 之后的期望索引集（显式索引；不含 SQLite 自动主键索引）。
# project_id 索引是 052 的漂移修复项：ORM 一直声明 index=True，但历史
# 迁移从未在数据库建过——052 之前迁移库没有它。
COMPOSITE = "ix_notifications_recipient_updated"
EXPECTED_AFTER_052 = {
    COMPOSITE,
    "ix_notifications_project_id",
    "ix_notifications_created_at",
}
DROPPED_BY_052 = {
    "ix_notifications_recipient_agent_id",
    "ix_notifications_read_at",
    "ix_notifications_category",
    "ix_notifications_group_key",
    "ix_notifications_fingerprint_version",
}
# 052 之前（009+023+038 之后）的迁移库显式索引集（无 project_id）。
EXPECTED_BEFORE_052 = (EXPECTED_AFTER_052 - {COMPOSITE, "ix_notifications_project_id"}) | DROPPED_BY_052


def _make_alembic_config(db_url: str) -> AlembicConfig:
    os.environ["MAP_DATABASE_URL"] = db_url
    get_settings.cache_clear()
    cfg = AlembicConfig("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _index_names(eng) -> set[str]:
    return {
        idx["name"]
        for idx in inspect(eng).get_indexes("notifications")
        if idx["name"]
    }


def test_052_upgrade_drops_singles_and_adds_composite(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 't11.db'}"
    cfg = _make_alembic_config(db_url)

    command.upgrade(cfg, "051")
    eng = create_engine(db_url)
    try:
        assert _index_names(eng) == EXPECTED_BEFORE_052

        command.upgrade(cfg, "head")
        after = _index_names(eng)
        assert COMPOSITE in after
        assert after == EXPECTED_AFTER_052

        # down：单列索引恢复、复合索引移除。
        command.downgrade(cfg, "051")
        assert _index_names(eng) == EXPECTED_BEFORE_052
        # 再 up 保持幂等（guard 分支不重复建/删）。
        command.upgrade(cfg, "head")
        assert _index_names(eng) == EXPECTED_AFTER_052
    finally:
        eng.dispose()
        os.environ.pop("MAP_DATABASE_URL", None)
        get_settings.cache_clear()


def test_orm_create_all_matches_migrated_index_set(engine) -> None:
    """ORM ``create_all``（dev/test 路径）与迁移库的索引集必须一致。

    模型侧漏改 ``index=True`` 或漏声明复合索引时，本测试先于任何
    「迁移库 vs 测试库行为不一致」的隐蔽问题失败。
    """
    assert _index_names(engine) == EXPECTED_AFTER_052


def test_composite_index_column_order(engine) -> None:
    """复合索引的列序正确：(recipient_agent_id, updated_at)。"""
    indexes = {
        idx["name"]: idx["column_names"]
        for idx in inspect(engine).get_indexes("notifications")
    }
    assert indexes[COMPOSITE] == ["recipient_agent_id", "updated_at"]


def test_list_query_uses_composite_index(engine, db_session) -> None:
    """SQLite 查询计划守卫：recipient 排序分页走复合索引，不再 filesort。"""
    project = Project(
        project_key="t11-plan",
        name="T11 Plan",
        workspace_path="/tmp/t11-plan",
    )
    db_session.add(project)
    db_session.flush()
    agent, _ = create_agent(db_session, "t11-agent", AgentRole.agent, project_id=project.id)
    for i in range(3):
        db_session.add(
            Notification(
                recipient_agent_id=agent.id,
                event="experiment.phase_changed",
                summary=f"n{i}",
                target_type="experiment",
            )
        )
    db_session.flush()

    # 与 list_for_agent 相同形态的查询；EXPLAIN QUERY PLAN 应命中复合索引。
    # ORDER BY 是 (updated_at DESC, created_at DESC) 双项——复合索引吃掉
    # 首项后，SQLite 仅对平局末项 created_at 做 temp b-tree（"FOR LAST
    # TERM OF ORDER BY"），这是两列索引下的最优计划；禁止的是全量
    # filesort（"FOR ORDER BY"，即裁剪前仅 recipient 单列索引时的形态）。
    plan_rows = db_session.execute(
        text(
            "EXPLAIN QUERY PLAN SELECT * FROM notifications "
            "WHERE recipient_agent_id = :rid "
            "ORDER BY updated_at DESC, created_at DESC LIMIT 50"
        ),
        {"rid": str(agent.id)},
    ).all()
    plan_text = " ".join(str(row) for row in plan_rows)
    assert COMPOSITE in plan_text, plan_text
    assert "USE TEMP B-TREE FOR ORDER BY" not in plan_text.upper(), plan_text


def test_upsert_still_hits_unique_constraint(db_session) -> None:
    """裁剪 group_key 单列索引后，upsert 冲突目标仍由 UNIQUE 约束服务。"""
    project = Project(
        project_key="t11-upsert",
        name="T11 Upsert",
        workspace_path="/tmp/t11-upsert",
    )
    db_session.add(project)
    db_session.flush()
    agent, _ = create_agent(db_session, "t11-upsert-agent", AgentRole.agent, project_id=project.id)

    base = {
        "recipient_agent_id": agent.id,
        "project_id": project.id,
        "event": "experiment.lock.no_progress",
        "summary": "s",
        "target_type": "experiment",
        "target_id": None,
        "group_key": "gk-1",
    }
    stmt = sqlite_insert(Notification).values(**base)
    stmt = stmt.on_conflict_do_update(
        index_elements=["recipient_agent_id", "group_key"],
        set_={"summary": stmt.excluded.summary},
    )
    db_session.execute(stmt)
    db_session.execute(stmt)  # 第二次走 ON CONFLICT 合并而非报错。
    rows = db_session.query(Notification).filter_by(group_key="gk-1").all()
    assert len(rows) == 1


def test_recipient_filter_queries_still_work(db_session) -> None:
    """左前缀覆盖：纯 recipient 等值与 unread 过滤在裁剪后行为不变。"""
    project = Project(
        project_key="t11-filter",
        name="T11 Filter",
        workspace_path="/tmp/t11-filter",
    )
    db_session.add(project)
    db_session.flush()
    agent, _ = create_agent(db_session, "t11-filter-agent", AgentRole.agent, project_id=project.id)
    other, _ = create_agent(db_session, "t11-other-agent", AgentRole.agent, project_id=project.id)

    def _notif(recipient_id, *, read=False) -> Notification:
        from datetime import datetime, timezone

        return Notification(
            recipient_agent_id=recipient_id,
            event="agent.mentioned",
            summary="m",
            target_type="topic_comment",
            read_at=datetime.now(timezone.utc) if read else None,
        )

    db_session.add(_notif(agent.id))
    db_session.add(_notif(agent.id, read=True))
    db_session.add(_notif(other.id))
    db_session.flush()

    unread = (
        db_session.query(Notification)
        .filter(
            Notification.recipient_agent_id == agent.id,
            Notification.read_at.is_(None),
        )
        .count()
    )
    assert unread == 1
    total = (
        db_session.query(Notification)
        .filter(Notification.recipient_agent_id == agent.id)
        .count()
    )
    assert total == 2
