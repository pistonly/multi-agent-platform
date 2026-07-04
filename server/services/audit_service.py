from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from map_types.schemas import ActionItemStalePayload
from server.domain.models import AuditLog, TopicActionItem
from server.domain.schemas import AuditLogRead


def _log_no_commit(
    db: Session,
    *,
    action: str,
    target_type: str,
    agent_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    summary: str | None = None,
    payload: dict | None = None,
) -> AuditLog:
    entry = AuditLog(
        agent_id=agent_id,
        project_id=project_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        summary=summary,
        payload_json=payload,
    )
    db.add(entry)
    db.flush()
    return entry


def log(
    db: Session,
    *,
    action: str,
    target_type: str,
    agent_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    summary: str | None = None,
    payload: dict | None = None,
) -> AuditLog:
    entry = _log_no_commit(
        db,
        action=action,
        target_type=target_type,
        agent_id=agent_id,
        project_id=project_id,
        target_id=target_id,
        summary=summary,
        payload=payload,
    )
    db.commit()
    db.refresh(entry)
    return entry


def query_by_target(
    db: Session,
    target_type: str,
    target_id: uuid.UUID,
) -> list[AuditLogRead]:
    stmt = (
        select(AuditLog)
        .where(AuditLog.target_type == target_type, AuditLog.target_id == target_id)
        .order_by(AuditLog.created_at.desc())
    )
    return [AuditLogRead.model_validate(row) for row in db.scalars(stmt)]


def query_all(db: Session, *, page: int = 1, page_size: int = 50) -> tuple[list[AuditLogRead], int]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    items = list(db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)))
    return [AuditLogRead.model_validate(row) for row in items], total


# --- Action-item audit events (experiment B / waker escalation 三段式) -----

# Action types live as constants so callers and grep share one source of truth.
# Stale is a *diagnostic* signal, not a state transition: ``complete`` and
# ``cancelled`` flip ``status``; ``stale`` only annotates that the assignee has
# not responded across the three-stage escalation window. ``wake_sent`` marks
# each waker firing so reviewers can grep the escalation timeline.
ACTION_ITEM_STALE = "action_item.stale"
ACTION_ITEM_WAKE_SENT = "action_item.wake_sent"


def _log_action_item_stale_no_commit(
    db: Session,
    *,
    item: TopicActionItem,
    stale_after_attempt: int,
    admin_notified: bool,
    creator_audit_only: bool,
) -> AuditLog:
    """Insert the ``action_item.stale`` audit row without committing.

    Used by ``action_item_service.mark_stale_no_commit`` which must keep the
    status mutation, the ``stale_at`` timestamp, and the audit row in one
    transaction (plan §3 §4 "wake_count 自增与 stale_at 的事务边界" risk).

    Validation mirrors the public ``log_action_item_stale`` helper — any
    rejection raises ``ValueError`` *before* the flush, so a partial insert
    cannot leak.
    """
    if item.owner_agent_id is None:
        raise ValueError(
            "action_item.stale requires owner_agent_id (assignee) — "
            "waker only wakes the owner, so stale cannot fire for unassigned items"
        )
    if item.last_woken_at is None:
        raise ValueError(
            "action_item.stale requires item.last_woken_at — "
            "stale fires only after at least one wake has happened"
        )
    if stale_after_attempt < 1:
        raise ValueError(
            f"stale_after_attempt must be >= 1 (got {stale_after_attempt})"
        )

    payload = ActionItemStalePayload(
        action_item_id=item.id,
        owner_agent_id=item.owner_agent_id,
        topic_id=item.topic_id,
        decision_id=item.decision_id,
        linked_experiment_id=item.linked_experiment_id,
        last_woken_at=item.last_woken_at,
        wake_count=item.wake_count,
        stale_after_attempt=stale_after_attempt,
        admin_notified=admin_notified,
        creator_audit_only=creator_audit_only,
    )
    return _log_no_commit(
        db,
        action=ACTION_ITEM_STALE,
        target_type="topic_action_item",
        agent_id=item.owner_agent_id,
        project_id=item.project_id,
        target_id=item.id,
        summary=f"行动项「{item.title}」进入 stale 路径（第 {stale_after_attempt} 次唤醒后无响应）",
        payload=payload.model_dump(mode="json"),
    )


def log_action_item_stale(
    db: Session,
    *,
    item: TopicActionItem,
    stale_after_attempt: int,
    admin_notified: bool,
    creator_audit_only: bool,
) -> AuditLog:
    """Write the ``action_item.stale`` audit event after the 4th unanswered wake.

    The runtime-waker stops waking the assignee once ``stale_at`` is set on the
    item; this event is the diagnostic signal that the open item has not
    progressed through T+24h → T+72h → every 7d up to 4 times. See plan §1.

    Payload is validated against ``ActionItemStalePayload`` before being
    written so the audit log stays grep-friendly (B-11 acceptance: payload
    carries ``wake_count`` so reviewers can confirm "走了 stale 路径" without
    joining other tables).

    Autocommit variant — prefer ``_log_action_item_stale_no_commit`` from
    inside a larger service transaction (see ``action_item_service.mark_stale_no_commit``).
    """
    entry = _log_action_item_stale_no_commit(
        db,
        item=item,
        stale_after_attempt=stale_after_attempt,
        admin_notified=admin_notified,
        creator_audit_only=creator_audit_only,
    )
    db.commit()
    db.refresh(entry)
    return entry


def log_action_item_wake_sent(
    db: Session,
    *,
    item: TopicActionItem,
    now: datetime,
) -> AuditLog:
    """Write the ``action_item.wake_sent`` audit row for one wake cycle.

    Audit-only signal — the durable side-effect (``wake_count += 1``,
    ``last_woken_at = now``) is applied by ``action_item_service.mark_wake_sent``.
    Centralising the audit shape here keeps the payload contract in one place.

    The audit row also gets ``wake_count`` after the increment so reviewers can
    reconstruct "the Nth wake" without joining the action_item table.
    """
    return _log_no_commit(
        db,
        action=ACTION_ITEM_WAKE_SENT,
        target_type="topic_action_item",
        agent_id=item.owner_agent_id,
        project_id=item.project_id,
        target_id=item.id,
        summary=f"行动项「{item.title}」第 {item.wake_count} 次唤醒",
        payload={
            "action_item_id": str(item.id),
            "owner_agent_id": str(item.owner_agent_id) if item.owner_agent_id else None,
            "topic_id": str(item.topic_id),
            "wake_count": item.wake_count,
            "last_woken_at": item.last_woken_at.isoformat() if item.last_woken_at else None,
        },
    )
