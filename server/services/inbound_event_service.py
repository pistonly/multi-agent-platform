"""Service layer for the runtime-waker inbound-event access log.

This module backs the D6 server-side record endpoint
(``POST /agents/me/inbound-events``), which is the primary dedup gate for the
runtime-waker: every waker must call this endpoint *before* resuming a session
so the server's ``UNIQUE(fingerprint)`` constraint enforces A1 (replay rejection)
and A2 (concurrent dedup) across processes and restarts.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.domain.models import Agent, InboundEvent
from server.domain.schemas import InboundEventCreate


def record_inbound_event(
    db: Session, agent: Agent, payload: InboundEventCreate
) -> tuple[InboundEvent, str]:
    """Persist a waker's record of an inbound notification.

    Returns ``(event, "recorded")`` on first time. If the ``UNIQUE(fingerprint)``
    constraint rejects the insert (replay / concurrent claim), the existing row
    is fetched and returned with ``(existing, "duplicate")``. Callers should
    map "duplicate" to a 409 Conflict response (D6 / A1 gate).

    The fetch-after-rollback is a best-effort lookup: in the rare race where the
    conflicting row is deleted before we look it up we re-raise the original
    IntegrityError so the caller still sees a 5xx rather than silently lying.
    """
    event = InboundEvent(
        agent_id=agent.id,
        event_id=payload.event_id,
        event_type=payload.event_type,
        source=payload.source,
        fingerprint=payload.fingerprint,
        payload=payload.payload,
    )
    db.add(event)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(InboundEvent)
            .filter(InboundEvent.fingerprint == payload.fingerprint)
            .one_or_none()
        )
        if existing is None:
            raise
        return existing, "duplicate"
    db.refresh(event)
    return event, "recorded"
