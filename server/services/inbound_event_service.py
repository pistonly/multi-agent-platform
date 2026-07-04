"""Service layer for the runtime-waker inbound-event access log.

This module backs the D6 server-side record endpoint
(``POST /agents/me/inbound-events``), which is the primary dedup gate for the
runtime-waker: every waker must call this endpoint *before* resuming a session
so the server's ``UNIQUE(fingerprint)`` constraint enforces A1 (replay rejection)
and A2 (concurrent dedup) across processes and restarts.

v0.9 (M30A/M31 I2): legacy v1 fingerprints (``inbound:<event_id>``) are
detected via :func:`notification_service.is_legacy_v1_fingerprint` and routed
to a separate code path that bumps ``InboundEvent.rejection_count`` instead of
returning the normal ``UNIQUE(fingerprint)`` 409 — the audit row is preserved
(matching plan D6 three-layer join) but the waker is told to skip resume.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.domain.models import Agent, InboundEvent
from server.domain.schemas import InboundEventCreate
from server.services.notification_service import is_legacy_v1_fingerprint


def record_inbound_event(
    db: Session, agent: Agent, payload: InboundEventCreate
) -> tuple[InboundEvent, str]:
    """Persist a waker's record of an inbound notification.

    Returns one of:

    - ``(event, "recorded")`` on first time (status code 201 at the API layer).
    - ``(existing, "duplicate")`` when the ``UNIQUE(fingerprint)`` constraint
      rejects the insert (replay / concurrent claim); API maps to 409 Conflict
      (D6 / A1 gate).
    - ``(event, "rejected_v1")`` when the fingerprint uses the legacy
      ``inbound:<event_id>`` shape. The row is upserted with
      ``rejection_count`` bumped, so the audit table still records the
      sighting; the waker is told to skip resume (API returns 200 OK with the
      current ``rejection_count`` so the caller can observe it).

    The fetch-after-rollback is a best-effort lookup: in the rare race where
    the conflicting row is deleted before we look it up we re-raise the
    original IntegrityError so the caller still sees a 5xx rather than silently
    lying.
    """
    if is_legacy_v1_fingerprint(payload.fingerprint):
        return _record_or_reject_v1(db, agent, payload)

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


def _record_or_reject_v1(
    db: Session, agent: Agent, payload: InboundEventCreate
) -> tuple[InboundEvent, str]:
    """Insert or upsert a legacy v1 fingerprint with ``rejection_count`` bumped.

    v1 fingerprints are pre-v0.9 namespaced (``inbound:<event_id>``) and MUST
    NOT trigger a session resume. We still persist an ``InboundEvent`` row so
    the audit table records the sighting — every repeat of the same fingerprint
    increments ``rejection_count`` so an operator can observe how often the
    legacy path fires. The waker distinguishes ``"rejected_v1"`` from
    ``"duplicate"`` and uses it to log a ``[wake:skip] v1 fingerprint rejected``
    line rather than treating it as a cross-process race.
    """
    existing = db.scalar(
        select(InboundEvent).where(InboundEvent.fingerprint == payload.fingerprint)
    )
    if existing is not None:
        existing.rejection_count = (existing.rejection_count or 0) + 1
        db.commit()
        db.refresh(existing)
        return existing, "rejected_v1"

    event = InboundEvent(
        agent_id=agent.id,
        event_id=payload.event_id,
        event_type=payload.event_type,
        source=payload.source,
        fingerprint=payload.fingerprint,
        payload=payload.payload,
        rejection_count=1,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event, "rejected_v1"
