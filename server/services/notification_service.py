import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from server.domain.models import Agent, AgentRole, Notification
from server.services.errors import ForbiddenError, NotFoundError


def _recipients_for_project(db: Session, project_id: uuid.UUID | None, exclude_agent_id: uuid.UUID) -> list[Agent]:
    if project_id is None:
        return []
    stmt = select(Agent).where(
        or_(
            Agent.project_id == project_id,
            Agent.role == AgentRole.admin,
        )
    )
    return [agent for agent in db.scalars(stmt) if agent.id != exclude_agent_id]


def enqueue_from_event(
    db: Session,
    *,
    project_id: uuid.UUID | None,
    actor_id: uuid.UUID,
    event: str,
    summary: str,
    target_type: str,
    target_id: uuid.UUID | None,
    payload: dict | None,
) -> list[uuid.UUID]:
    """Write in-app notifications for project agents (and admins), excluding the actor."""
    recipients = _recipients_for_project(db, project_id, actor_id)
    if not recipients:
        return []

    notification_ids: list[uuid.UUID] = []
    for recipient in recipients:
        notification = Notification(
            recipient_agent_id=recipient.id,
            project_id=project_id,
            event=event,
            summary=summary,
            target_type=target_type,
            target_id=target_id,
            payload_json=payload,
        )
        db.add(notification)
        db.flush()
        notification_ids.append(notification.id)
    db.commit()
    return notification_ids


def enqueue_for_agents(
    db: Session,
    *,
    recipient_agent_ids: list[uuid.UUID],
    project_id: uuid.UUID | None,
    actor_id: uuid.UUID,
    event: str,
    summary: str,
    target_type: str,
    target_id: uuid.UUID | None,
    payload: dict | None,
) -> list[uuid.UUID]:
    """Write in-app notifications for specific agents (e.g. @mentions)."""
    notification_ids: list[uuid.UUID] = []
    for recipient_id in recipient_agent_ids:
        if recipient_id == actor_id:
            continue
        notification = Notification(
            recipient_agent_id=recipient_id,
            project_id=project_id,
            event=event,
            summary=summary,
            target_type=target_type,
            target_id=target_id,
            payload_json=payload,
        )
        db.add(notification)
        db.flush()
        notification_ids.append(notification.id)
    if notification_ids:
        db.commit()
    return notification_ids


def list_for_agent(
    db: Session,
    agent: Agent,
    *,
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Notification], int]:
    filters = [Notification.recipient_agent_id == agent.id]
    if unread_only:
        filters.append(Notification.read_at.is_(None))
    total = db.scalar(select(func.count()).select_from(Notification).where(*filters)) or 0
    rows = list(
        db.scalars(
            select(Notification)
            .where(*filters)
            .order_by(Notification.created_at.desc())
            .offset(offset)
            .limit(min(limit, 200))
        )
    )
    return rows, total


def count_unread(db: Session, agent: Agent) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.recipient_agent_id == agent.id, Notification.read_at.is_(None))
        )
        or 0
    )


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
    now = datetime.now(timezone.utc)
    rows = list(
        db.scalars(
            select(Notification).where(
                Notification.recipient_agent_id == agent.id,
                Notification.read_at.is_(None),
            )
        )
    )
    for row in rows:
        row.read_at = now
    if rows:
        db.commit()
    return len(rows)
