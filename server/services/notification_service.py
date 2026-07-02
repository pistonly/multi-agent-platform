import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from server.domain.models import Agent, AgentRole, Notification
from server.services import notification_stream
from server.services.errors import ForbiddenError, NotFoundError

# Phase 2 D2: persona agent names used by emit_kind to resolve wake-kind
# recipients. These are the canonical MAP persona agents bound to a project;
# the wake kinds (pending_review / pending_result_review / topic_lifecycle /
# etc.) target one or more of these so the waker can differentiate lifecycle
# events from generic notifications. Falls back to no recipients if a project
# has not yet bound a given persona (early onboarding is graceful).
PERSONA_AGENT_NAMES: dict[str, str] = {
    "host": "multi-agents-platform-host",
    "participant": "multi-agents-platform-participant",
    "reviewer": "multi-agents-platform-reviewer",
}


def _emit_created(
    recipient_ids: list[uuid.UUID],
    notification_ids: list[uuid.UUID],
    *,
    event: str,
) -> None:
    for recipient_id, notification_id in zip(recipient_ids, notification_ids, strict=True):
        notification_stream.publish(
            recipient_id,
            {
                "type": "notification.created",
                "event": event,
                "notification_id": str(notification_id),
            },
        )


def _resolve_persona_agent_ids(
    db: Session, project_id: uuid.UUID, personas: list[str]
) -> list[uuid.UUID]:
    """Resolve persona names to Agent.id within a project.

    Returns an empty list if no persona matches (e.g. project hasn't bound
    that persona yet) so callers can treat it as a no-op rather than a 500.
    """
    names = [PERSONA_AGENT_NAMES[p] for p in personas if p in PERSONA_AGENT_NAMES]
    if not names:
        return []
    rows = db.scalars(
        select(Agent).where(Agent.name.in_(names), Agent.project_id == project_id)
    ).all()
    return [agent.id for agent in rows]


def emit_kind(
    db: Session,
    *,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    personas: list[str],
    event: str,
    summary: str,
    target_type: str,
    target_id: uuid.UUID,
    payload: dict | None,
) -> list[uuid.UUID]:
    """Insert one Notification per persona agent + SSE publish.

    Phase 2 D2 entry point for kind-specific wake events. Distinct from
    ``enqueue_from_event`` (broadcast to all project agents) — here the
    recipient set is narrowed to persona agents (host / participant /
    reviewer) so the waker can differentiate lifecycle wake kinds from
    the generic ``notification`` bucket.

    Payload is enriched with ``kind`` (derived from the event name's middle
    segment, e.g. ``topic.lifecycle.closed`` → ``lifecycle``) so the waker
    can build a kind-specific fingerprint without re-deriving from the
    Notification row.
    """
    recipient_ids = _resolve_persona_agent_ids(db, project_id, personas)
    if not recipient_ids:
        return []
    enriched = dict(payload or {})
    parts = event.split(".")
    if len(parts) >= 2 and "kind" not in enriched:
        enriched["kind"] = ".".join(parts[:-1])
    return enqueue_for_agents(
        db,
        recipient_agent_ids=recipient_ids,
        project_id=project_id,
        actor_id=actor_id,
        event=event,
        summary=summary,
        target_type=target_type,
        target_id=target_id,
        payload=enriched,
    )


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
    exclude_recipient_ids: set[uuid.UUID] | None = None,
) -> list[uuid.UUID]:
    """Write in-app notifications for project agents (and admins), excluding the actor."""
    skip = exclude_recipient_ids or set()
    recipients = [
        agent
        for agent in _recipients_for_project(db, project_id, actor_id)
        if agent.id not in skip
    ]
    if not recipients:
        return []

    notification_ids: list[uuid.UUID] = []
    recipient_ids: list[uuid.UUID] = []
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
        recipient_ids.append(recipient.id)
    db.commit()
    if notification_ids:
        _emit_created(recipient_ids, notification_ids, event=event)
    return notification_ids


def notify_topic_comment_created(
    db: Session,
    *,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    creator_agent_id: uuid.UUID,
    target_id: uuid.UUID,
    payload: dict | None,
) -> list[uuid.UUID]:
    """Broadcast to project agents; host gets a separate directed notification (no duplicate)."""
    event = "topic.comment.created"
    base_payload = dict(payload or {})
    notification_ids = enqueue_from_event(
        db,
        project_id=project_id,
        actor_id=actor_id,
        event=event,
        summary="话题新评论",
        target_type="topic_comment",
        target_id=target_id,
        payload=base_payload,
        exclude_recipient_ids={creator_agent_id} if creator_agent_id != actor_id else None,
    )
    if creator_agent_id != actor_id:
        notification_ids.extend(
            enqueue_for_agents(
                db,
                recipient_agent_ids=[creator_agent_id],
                project_id=project_id,
                actor_id=actor_id,
                event=event,
                summary="【主持】话题新评论待回复",
                target_type="topic_comment",
                target_id=target_id,
                payload={**base_payload, "host_directed": True},
            )
        )
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
    recipient_ids: list[uuid.UUID] = []
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
        recipient_ids.append(recipient_id)
    if notification_ids:
        db.commit()
        _emit_created(recipient_ids, notification_ids, event=event)
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
