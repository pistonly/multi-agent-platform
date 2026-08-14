"""Inbound (wake) event audit schemas."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from map_types.enums import InboundEventSource

from .base import ORMModel



# --- InboundEvent ---


class InboundEventCreate(BaseModel):
    """Waker-side record of a notification it intends to act on.

    ``fingerprint`` is the dedup key — server enforces ``UNIQUE(fingerprint)``
    and returns 409 Conflict on replay. ``event_id`` should match the upstream
    ``notification.id`` so the three audit layers stay joinable.
    """

    event_id: uuid.UUID
    event_type: str = Field(min_length=1, max_length=64)
    source: InboundEventSource = InboundEventSource.polling
    fingerprint: str = Field(min_length=1, max_length=128)
    payload: dict | None = None


class InboundEventRead(ORMModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    event_id: uuid.UUID
    event_type: str
    source: InboundEventSource
    fingerprint: str
    payload: dict | None
    received_at: datetime
    acked_at: datetime | None
    rejection_count: int = 0


class InboundEventRecordResult(BaseModel):
    """Return shape for ``POST /me/inbound-events``.

    ``status="recorded"`` → first time, waker may proceed.
    ``status="duplicate"`` → fingerprint already existed; treat as already woken
    (Phase 1 server gate; see plan D6).
    ``status="rejected_v1"`` → legacy v1 fingerprint (``inbound:<event_id>``);
    inbound_event row is persisted (or upserted) with ``rejection_count`` bumped
    so the audit table still records the sighting, but the waker MUST NOT
    resume — see plan I2 / M30A acceptance #4.
    """

    status: Literal["recorded", "duplicate", "rejected_v1"]
    event: InboundEventRead
