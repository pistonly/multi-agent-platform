"""Notification inbox 与 admin wake 通知簇 — T45 拆分自 notification_service.py。

职责边界：inbox 读取/已读（``list_for_agent`` / ``count_unread`` /
``mark_read`` / ``mark_all_read`` / mention 批量已读）与 action_item
admin 通知（``notify_owner_action_item_wake`` / ``notify_admin_action_item_stale``）。
fanout（upsert / emit_kind / enqueue_for_agents）留守
``notification_service``；admin wake 对 ``enqueue_for_agents`` 采用函数内
lazy import（与 ``notification_stalled`` 同一手法的反方向依赖），宿主
顶层 re-export 本模块，无导入环。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from map_types.enums import NotificationCategory
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from server.domain.models import Agent, AgentRole, Notification, TopicActionItem
from server.services.errors import ForbiddenError, NotFoundError


def list_for_agent(
    db: Session,
    agent: Agent,
    *,
    unread_only: bool = False,
    category: NotificationCategory | None = None,
    target_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Notification], int]:
    filters = [Notification.recipient_agent_id == agent.id]
    if unread_only:
        filters.append(Notification.read_at.is_(None))
    if category is not None:
        filters.append(Notification.category == category)
    if target_type is not None:
        filters.append(Notification.target_type == target_type)
    total = db.scalar(select(func.count()).select_from(Notification).where(*filters)) or 0
    rows = list(
        db.scalars(
            select(Notification)
            .where(*filters)
            .order_by(Notification.updated_at.desc(), Notification.created_at.desc())
            .offset(offset)
            .limit(min(limit, 200))
        )
    )
    return rows, total


def count_unread(
    db: Session,
    agent: Agent,
    *,
    category: NotificationCategory | None = None,
    target_type: str | None = None,
) -> int:
    filters = [Notification.recipient_agent_id == agent.id, Notification.read_at.is_(None)]
    if category is not None:
        filters.append(Notification.category == category)
    if target_type is not None:
        filters.append(Notification.target_type == target_type)
    return (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(*filters)
        )
        or 0
    )


def mark_agent_mentioned_notifications_read_no_commit(
    db: Session,
    *,
    mentions: list[Any],
    now: datetime | None = None,
) -> int:
    """Mark ``agent.mentioned`` notifications read when their mention is dismissed.

    T14（2026-08）：批量单条 UPDATE。原实现每个 mention 各发一条 SELECT
    再逐行赋值 ``read_at``（M 个 mention = M 条 SELECT + K 条 UPDATE）；
    现在先去重 (mentioned_agent_id, source_id) 对，再合成一条按
    ``event + 未读 + (recipient, target) 任一匹配`` 的核心 UPDATE，行数
    从 ``rowcount`` 取。不 commit，与调用方 ``_apply_mention_dismiss_cascade``
    的事务边界保持一致。
    """
    from server.domain.models import Mention

    if not mentions:
        return 0
    now = now or datetime.now(timezone.utc)
    # dict 充当有序去重集合：同一 (recipient, source) 的多条 mention 只留一个条件。
    pairs: dict[tuple[uuid.UUID, uuid.UUID], None] = {}
    for mention in mentions:
        if not isinstance(mention, Mention):
            continue
        pairs[(mention.mentioned_agent_id, mention.source_id)] = None
    if not pairs:
        return 0
    match_any = or_(
        *(
            and_(
                Notification.recipient_agent_id == recipient,
                Notification.target_id == source,
            )
            for recipient, source in pairs
        )
    )
    result = db.execute(
        update(Notification)
        .where(
            Notification.event == "agent.mentioned",
            Notification.read_at.is_(None),
            match_any,
        )
        .values(read_at=now)
    )
    return int(result.rowcount or 0)


def mark_read(db: Session, agent: Agent, notification_id: uuid.UUID) -> Notification:
    notification = db.get(Notification, notification_id)
    if notification is None:
        raise NotFoundError("Notification not found")
    if notification.recipient_agent_id != agent.id:
        raise ForbiddenError("Cannot mark another agent's notification")
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(notification)
    return notification


def mark_all_read(db: Session, agent: Agent) -> int:
    """Mark every unread notification of the agent read; return the count.

    T13（2026-08）：改为单条核心 UPDATE。原实现把全部未读行加载成 ORM
    对象再逐行赋值 ``read_at``，未读量大时内存与 SQL 双放大（N 行 =
    1 SELECT + N UPDATE）。核心 UPDATE 由数据库一次完成，行数从
    ``result.rowcount`` 取。commit 后 session 内已加载的 ORM 实例统一
    expire，同请求后续读取自动重载新值。
    """
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(Notification)
        .where(
            Notification.recipient_agent_id == agent.id,
            Notification.read_at.is_(None),
        )
        .values(read_at=now)
    )
    db.commit()
    return int(result.rowcount or 0)

# ---------------------------------------------------------------------------
# action_item wake / stale notification helpers (experiment B, plan §3 #2 #3)
# ---------------------------------------------------------------------------
#
# The runtime-waker calls ``mark_wake_sent`` / ``mark_stale`` via the API,
# but those endpoints only mutate the action_item + write the audit row.
# They do NOT post any in-app notification — by design, the wakeable
# signals the assignee / admin sees live here, alongside the audit row.
#
# Why split this out instead of folding into the endpoint wrappers:
# - Keeps ``action_item_service`` / ``topic_service`` free of notification
#   service imports (service layering: audit + state in service, fan-out in
#   notification service).
# - Makes the actor / recipient policy explicit per plan section §3 #2 #3:
#   wake goes to the assignee; stale goes to admins; creator is intentionally
#   skipped (audit-only path, see I6).
# ---------------------------------------------------------------------------


def _resolve_admin_agent_ids(
    db: Session,
    *,
    exclude_agent_id: uuid.UUID | None,
) -> list[uuid.UUID]:
    """Return the list of admin Agent ids in the system.

    Admin role is global in this codebase (no per-project scoping), so a
    single query against ``Agent.role == AgentRole.admin`` is sufficient.
    ``exclude_agent_id`` filters the assignee out so the owner does not get
    a duplicate notification through the admin path when the assignee is
    themselves an admin.
    """
    rows = db.scalars(select(Agent.id).where(Agent.role == AgentRole.admin)).all()
    if exclude_agent_id is None:
        return list(rows)
    return [row for row in rows if row != exclude_agent_id]


def notify_owner_action_item_wake(
    db: Session,
    *,
    action_item: TopicActionItem,
) -> list[uuid.UUID]:
    """Send a wakeable notification to the assignee after a wake bump.

    Plan §3 wake path: the runtime-waker wakes the owner at T+24h / T+72h /
    every 7d up to 4 times. Each bump surfaces here as an in-app notification
    so the assignee sees a wakeable signal in their todos (independent of
    whether they happen to be looking at the action_item list right now).
    Falls through silently when the item has no owner (shouldn't happen —
    ``mark_wake_sent`` already rejected unassigned items upstream — but the
    guard is here for defence-in-depth).
    """
    if action_item.owner_agent_id is None:
        return []
    payload: dict[str, object] = {
        "action_item_id": str(action_item.id),
        "topic_id": str(action_item.topic_id),
        "wake_count": action_item.wake_count,
    }
    if action_item.last_woken_at is not None:
        payload["last_woken_at"] = action_item.last_woken_at.isoformat()
    from server.services.notification_service import enqueue_for_agents

    return enqueue_for_agents(
        db,
        recipient_agent_ids=[action_item.owner_agent_id],
        project_id=action_item.project_id,
        actor_id=action_item.owner_agent_id,
        event="action_item.wake_sent",
        summary=f"待办提醒：{action_item.title}（第 {action_item.wake_count} 次）",
        target_type="topic_action_item",
        target_id=action_item.id,
        payload=payload,
        wakeable=True,
        exclude_actor=False,
    )


def notify_admin_action_item_stale(
    db: Session,
    *,
    action_item: TopicActionItem,
) -> list[uuid.UUID]:
    """Send a wakeable notification to every admin after a stale transition.

    Plan §3 #2 (3c admin 优先): admin is the most stable收口 because the
    creator may have been deactivated / changed roles / left the project.
    The owner is explicitly excluded — if the owner happens to be an admin
    they'd otherwise get a duplicate notification through both paths, and
    the audit row + plan §3 #3 (3c creator audit-only) policy says admin
    notification should not double as the owner's channel.

    Plan §3 #3 (creator audit-only): the creator is intentionally NOT in
    this recipient set. They can find the stale event via ``action_items``
    or the audit history, but they do NOT get a wake notification (per I6).
    """
    admin_ids = _resolve_admin_agent_ids(
        db, exclude_agent_id=action_item.owner_agent_id
    )
    if not admin_ids:
        return []
    payload: dict[str, object] = {
        "action_item_id": str(action_item.id),
        "topic_id": str(action_item.topic_id),
        "wake_count": action_item.wake_count,
    }
    if action_item.stale_at is not None:
        payload["stale_at"] = action_item.stale_at.isoformat()
    # actor_id is unused because exclude_actor=False — every admin in the
    # resolved list gets the notification regardless. We still need to pass
    # a UUID-shaped value to satisfy the signature, so use the first admin
    # or the owner as a stable label.
    actor_id = action_item.owner_agent_id or admin_ids[0]
    from server.services.notification_service import enqueue_for_agents

    return enqueue_for_agents(
        db,
        recipient_agent_ids=admin_ids,
        project_id=action_item.project_id,
        actor_id=actor_id,
        event="action_item.stale",
        summary=f"行动项已 stale：{action_item.title}",
        target_type="topic_action_item",
        target_id=action_item.id,
        payload=payload,
        wakeable=True,
        exclude_actor=False,
    )
