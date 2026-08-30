"""notification_cleanup 单测（实验 8b1d20a1 I4 — plan A5/A6 验收）。

覆盖：

1. **digest ≥7 天自动 read + 写 audit**：happy path
2. **digest <7 天不动**：边界条件
3. **wakeable 类不动**：obligation-wakeable 永远不自清
4. **obligation-exempt event 即使被误标 digest 也不动**：防御性兜底
5. **空库 → 返回 0**：幂等
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from map_types.enums import NotificationCategory

from server.domain.models import (
    Agent,
    AgentRole,
    Notification,
    Project,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(db_session) -> Project:
    project = Project(
        id=uuid.uuid4(),
        project_key=f"cleanup-test-{uuid.uuid4().hex[:8]}",
        name="cleanup test project",
        workspace_path="/tmp/cleanup-test",
    )
    db_session.add(project)
    db_session.flush()
    return project


def _make_agent(
    db_session,
    *,
    name: str = "recipient",
    project_id: uuid.UUID | None = None,
) -> Agent:
    agent = Agent(
        id=uuid.uuid4(),
        name=f"{name}-{uuid.uuid4().hex[:8]}",
        api_token_hash="x" * 64,
        api_token_prefix="test",
        project_id=project_id,
        role=AgentRole.agent,
    )
    db_session.add(agent)
    db_session.flush()
    return agent


def _make_notification(
    db_session,
    *,
    event: str,
    category: NotificationCategory,
    first_event_at: datetime,
    unread: bool = True,
) -> Notification:
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    notif = Notification(
        id=uuid.uuid4(),
        recipient_agent_id=agent.id,
        project_id=project.id,
        event=event,
        summary=f"{event} cleanup test",
        target_type="topic",
        target_id=uuid.uuid4(),
        payload_json={"event": event},
        category=category,
        group_key=f"cleanup-test:{event}:{uuid.uuid4().hex[:8]}",
        wake_version=1,
        fingerprint_version="v2",
        event_count=1,
        first_event_at=first_event_at,
        last_event_at=first_event_at,
        read_at=None if unread else datetime.now(timezone.utc),
    )
    db_session.add(notif)
    db_session.flush()
    return notif


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_digest_older_than_7d_is_read_and_audit_logged(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """digest 类 first_event_at 距今 8 天 → 自动 read + 写 audit_logs。"""
    from server.services.audit_service import query_by_target
    from server.services.notification_cleanup import cleanup_digest_notifications

    old = _now() - timedelta(days=8)
    notif = _make_notification(
        db_session,
        event="topic.comment.created",
        category=NotificationCategory.digest,
        first_event_at=old,
    )

    drained = cleanup_digest_notifications(db_session, now=_now())
    assert drained == 1

    db_session.refresh(notif)
    assert notif.read_at is not None

    # audit_logs 应该有一条
    rows = query_by_target(db_session, "notification", notif.id)
    assert any(r.action == "auto_drain_digest_7d" for r in rows)


def test_audit_log_payload_contains_event_and_reason(
    db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A6：审计日志 payload 含 who=system / when / why / target_id。"""
    from server.services.audit_service import query_by_target
    from server.services.notification_cleanup import cleanup_digest_notifications

    old = _now() - timedelta(days=10)
    notif = _make_notification(
        db_session,
        event="topic.comment.created",
        category=NotificationCategory.digest,
        first_event_at=old,
    )

    cleanup_digest_notifications(db_session, now=_now())
    rows = query_by_target(db_session, "notification", notif.id)
    audit = next(r for r in rows if r.action == "auto_drain_digest_7d")
    payload = audit.payload_json or {}
    assert payload.get("actor") == "system"
    assert payload.get("reason") == "auto_drain_digest_7d"
    assert payload.get("event") == "topic.comment.created"
    assert audit.target_id == notif.id


# ---------------------------------------------------------------------------
# 边界条件
# ---------------------------------------------------------------------------


def test_digest_under_7d_is_not_read(db_session) -> None:
    """digest 类 6 天前 → 不动。"""
    from server.services.notification_cleanup import cleanup_digest_notifications

    recent = _now() - timedelta(days=6)
    notif = _make_notification(
        db_session,
        event="topic.comment.created",
        category=NotificationCategory.digest,
        first_event_at=recent,
    )

    drained = cleanup_digest_notifications(db_session, now=_now())
    assert drained == 0

    db_session.refresh(notif)
    assert notif.read_at is None


def test_digest_exactly_at_cutoff_is_read(db_session) -> None:
    """恰好 7 天整（first_event_at == cutoff）→ 必读（≤ 边界）。"""
    from server.services.notification_cleanup import cleanup_digest_notifications

    fixed_now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
    notif = _make_notification(
        db_session,
        event="topic.comment.created",
        category=NotificationCategory.digest,
        first_event_at=fixed_now - timedelta(days=7),
    )

    drained = cleanup_digest_notifications(db_session, now=fixed_now)
    assert drained == 1

    db_session.refresh(notif)
    assert notif.read_at is not None


def test_digest_one_second_before_cutoff_is_not_read(db_session) -> None:
    """first_event_at = cutoff + 1s（6 天 23h 59m 59s）→ 不动。"""
    from server.services.notification_cleanup import cleanup_digest_notifications

    fixed_now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
    notif = _make_notification(
        db_session,
        event="topic.comment.created",
        category=NotificationCategory.digest,
        first_event_at=fixed_now - timedelta(days=7) + timedelta(seconds=1),
    )

    drained = cleanup_digest_notifications(db_session, now=fixed_now)
    assert drained == 0

    db_session.refresh(notif)
    assert notif.read_at is None


def test_already_read_notification_not_touched(db_session) -> None:
    """已 read 的 notification 不被重复处理。"""
    from server.services.notification_cleanup import cleanup_digest_notifications

    old = _now() - timedelta(days=30)
    _make_notification(
        db_session,
        event="topic.comment.created",
        category=NotificationCategory.digest,
        first_event_at=old,
        unread=False,
    )

    drained = cleanup_digest_notifications(db_session, now=_now())
    assert drained == 0


# ---------------------------------------------------------------------------
# Wakeable / obligation 永远不动
# ---------------------------------------------------------------------------


def test_wakeable_older_than_7d_is_not_read(db_session) -> None:
    """wakeable 类即便 ≥7 天也不动（D5 硬边界）。"""
    from server.services.notification_cleanup import cleanup_digest_notifications

    old = _now() - timedelta(days=30)
    notif = _make_notification(
        db_session,
        event="experiment.lifecycle.withdrawn",
        category=NotificationCategory.wakeable,
        first_event_at=old,
    )

    drained = cleanup_digest_notifications(db_session, now=_now())
    assert drained == 0

    db_session.refresh(notif)
    assert notif.read_at is None


def test_obligation_exempt_event_mislabeled_as_digest_skipped(
    db_session, caplog: pytest.LogCaptureFixture
) -> None:
    """obligation-exempt event 即便被误标为 digest → 跳过 + warning。

    plan D5 双层安全网：category=digest 是首要判定，但 obligation-exempt
    event 是第二层兜底——若 schema 漂移导致误标也必须跳过。
    """
    import logging

    from server.services.notification_cleanup import cleanup_digest_notifications

    old = _now() - timedelta(days=30)
    notif = _make_notification(
        db_session,
        event="experiment.lifecycle.cancelled",
        category=NotificationCategory.digest,  # 误标——classify 应保 wakeable
        first_event_at=old,
    )

    with caplog.at_level(logging.WARNING, logger="server.services.notification_cleanup"):
        drained = cleanup_digest_notifications(db_session, now=_now())
    assert drained == 0

    db_session.refresh(notif)
    assert notif.read_at is None


# ---------------------------------------------------------------------------
# 幂等 / 边界
# ---------------------------------------------------------------------------


def test_empty_database_returns_zero(db_session) -> None:
    """空库调用 → 返回 0，不抛异常。"""
    from server.services.notification_cleanup import cleanup_digest_notifications

    drained = cleanup_digest_notifications(db_session, now=_now())
    assert drained == 0


def test_notification_without_first_event_at_not_touched(db_session) -> None:
    """first_event_at 为 None（schema 漂移防御）→ 不动。"""
    from server.services.notification_cleanup import cleanup_digest_notifications

    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    notif = Notification(
        id=uuid.uuid4(),
        recipient_agent_id=agent.id,
        project_id=project.id,
        event="topic.comment.created",
        summary="missing first_event_at",
        target_type="topic",
        target_id=uuid.uuid4(),
        payload_json={},
        category=NotificationCategory.digest,
        group_key=f"missing:{uuid.uuid4().hex[:8]}",
        wake_version=1,
        fingerprint_version="v2",
        event_count=1,
        first_event_at=None,  # 漂移场景
        last_event_at=None,
        read_at=None,
    )
    db_session.add(notif)
    db_session.flush()

    drained = cleanup_digest_notifications(db_session, now=_now())
    assert drained == 0
