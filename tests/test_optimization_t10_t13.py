"""优化任务 T13 / T10 的回归测试（见 docs/OPTIMIZATION-TASKS.md）。

T13：``mark_all_read`` 改单条核心 UPDATE——N 条未读 = 恒定 2 条 SQL
（1 UPDATE + commit/savepoint 簿记），不再随行数放大 SELECT + N UPDATE。

T10：``GET /status`` 进程内短 TTL 缓存——TTL 内二次调用零 SQL、
TTL 过期 / reset 后重建、TTL=0 时完全关闭、不同 project_id 键独立。
"""

from __future__ import annotations

import uuid as uuid_mod
from datetime import datetime, timezone

from server.config import get_settings
from server.domain.models import AgentRole, Notification, Project
from server.services import notification_service, status_service
from server.services.auth import create_agent
from tests.test_perf_count_sql_fixture import count_sql_calls


def _make_project(db_session) -> Project:
    project = Project(
        project_key=f"opt-{uuid_mod.uuid4().hex[:8]}",
        name="Optimization Tests",
        workspace_path="/tmp/opt-tests",
    )
    db_session.add(project)
    db_session.flush()
    return project


def _make_notification(
    db_session,
    *,
    recipient_id,
    event: str = "experiment.phase_changed",
    read: bool = False,
) -> Notification:
    row = Notification(
        recipient_agent_id=recipient_id,
        event=event,
        summary=f"notif-{uuid_mod.uuid4().hex[:6]}",
        target_type="experiment",
        read_at=datetime.now(timezone.utc) if read else None,
    )
    db_session.add(row)
    return row


# ---------------------------------------------------------------------------
# T13: mark_all_read 单条 UPDATE
# ---------------------------------------------------------------------------


def test_mark_all_read_marks_only_own_unread(db_session):
    project = _make_project(db_session)
    agent, _ = create_agent(db_session, "t13-owner", AgentRole.agent, project_id=project.id)
    other, _ = create_agent(db_session, "t13-other", AgentRole.agent, project_id=project.id)

    _make_notification(db_session, recipient_id=agent.id)  # 未读
    _make_notification(db_session, recipient_id=agent.id, read=True)  # 已读
    _make_notification(db_session, recipient_id=other.id)  # 他人未读
    db_session.flush()

    marked = notification_service.mark_all_read(db_session, agent)
    assert marked == 1

    agent_rows = db_session.query(Notification).filter(
        Notification.recipient_agent_id == agent.id
    ).all()
    assert all(r.read_at is not None for r in agent_rows)
    other_unread = (
        db_session.query(Notification)
        .filter(Notification.recipient_agent_id == other.id, Notification.read_at.is_(None))
        .count()
    )
    assert other_unread == 1


def test_mark_all_read_returns_zero_when_nothing_unread(db_session):
    project = _make_project(db_session)
    agent, _ = create_agent(db_session, "t13-empty", AgentRole.agent, project_id=project.id)
    db_session.flush()
    assert notification_service.mark_all_read(db_session, agent) == 0


def test_mark_all_read_sql_count_is_constant(db_session, engine):
    """T13 回归守卫：无论多少未读行，只允许恰好 1 条 UPDATE 且 0 条 SELECT。"""
    project = _make_project(db_session)
    agent, _ = create_agent(db_session, "t13-bulk", AgentRole.agent, project_id=project.id)
    for _ in range(5):
        _make_notification(db_session, recipient_id=agent.id)
    db_session.flush()

    with count_sql_calls(engine, label="t13.mark_all_read") as counter:
        marked = notification_service.mark_all_read(db_session, agent)

    assert marked == 5
    updates = [s for s, _ in counter.statements if s.lstrip().upper().startswith("UPDATE")]
    selects = [s for s, _ in counter.statements if s.lstrip().upper().startswith("SELECT")]
    assert len(updates) == 1, counter.summary()
    assert selects == [], counter.summary()


# ---------------------------------------------------------------------------
# T10: /status 短 TTL 缓存
# ---------------------------------------------------------------------------


def _patch_ttl(monkeypatch, ttl_seconds: int):
    """把 server.config.get_settings 换成指定 TTL 的副本（惰性导入在调用期解析）。"""
    patched = get_settings().model_copy(update={"status_cache_ttl_seconds": ttl_seconds})
    monkeypatch.setattr("server.config.get_settings", lambda: patched)


def test_status_cache_hits_within_ttl(db_session, engine, monkeypatch):
    project = _make_project(db_session)
    create_agent(db_session, "t10-host", AgentRole.agent, project_id=project.id)
    db_session.flush()
    _patch_ttl(monkeypatch, 60)

    first = status_service.get_global_status(db_session, project_id=project.id)
    with count_sql_calls(engine, label="t10.cache-hit") as counter:
        second = status_service.get_global_status(db_session, project_id=project.id)
    assert counter.count == 0, counter.summary()
    assert second is first


def test_status_cache_rebuilds_after_reset(db_session, engine, monkeypatch):
    project = _make_project(db_session)
    create_agent(db_session, "t10-reset", AgentRole.agent, project_id=project.id)
    db_session.flush()
    _patch_ttl(monkeypatch, 60)

    first = status_service.get_global_status(db_session, project_id=project.id)
    status_service.reset_status_cache()
    second = status_service.get_global_status(db_session, project_id=project.id)
    assert second is not first


def test_status_cache_rebuilds_after_ttl_expiry(db_session, monkeypatch):
    project = _make_project(db_session)
    create_agent(db_session, "t10-expiry", AgentRole.agent, project_id=project.id)
    db_session.flush()
    _patch_ttl(monkeypatch, 60)

    first = status_service.get_global_status(db_session, project_id=project.id)
    # 白盒：把已存时间戳拨回过去，模拟 TTL 过期（避免真实 sleep）。
    stored = status_service._status_cache[project.id]
    status_service._status_cache[project.id] = (stored[0] - 10_000, stored[1])
    second = status_service.get_global_status(db_session, project_id=project.id)
    assert second is not first


def test_status_cache_disabled_when_ttl_zero(db_session, monkeypatch):
    project = _make_project(db_session)
    create_agent(db_session, "t10-off", AgentRole.agent, project_id=project.id)
    db_session.flush()
    _patch_ttl(monkeypatch, 0)

    first = status_service.get_global_status(db_session, project_id=project.id)
    second = status_service.get_global_status(db_session, project_id=project.id)
    assert second is not first
    assert status_service._status_cache == {}


def test_status_cache_keys_are_per_project(db_session, monkeypatch):
    project_a = _make_project(db_session)
    project_b = _make_project(db_session)
    db_session.flush()
    _patch_ttl(monkeypatch, 60)

    a1 = status_service.get_global_status(db_session, project_id=project_a.id)
    b1 = status_service.get_global_status(db_session, project_id=project_b.id)
    a2 = status_service.get_global_status(db_session, project_id=project_a.id)
    # B 的构建不应挤掉 A 的缓存：A 在 TTL 内仍命中同一实例。
    assert a2 is a1
    assert b1 is not a1


def test_status_endpoint_serves_cached_snapshot(client, admin_headers, monkeypatch):
    """API 层冒烟：连续两次 GET /status 均 200（第二次走缓存路径）。"""
    _patch_ttl(monkeypatch, 60)
    first = client.get("/api/v1/status", headers=admin_headers)
    second = client.get("/api/v1/status", headers=admin_headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
