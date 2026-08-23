"""Notification schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from map_types.enums import NotificationCategory, NotificationFingerprintVersion

from .base import ORMModel

# --- Notification ---


class NotificationRead(ORMModel):
    id: uuid.UUID
    recipient_agent_id: uuid.UUID
    project_id: uuid.UUID | None
    event: str
    summary: str
    target_type: str
    target_id: uuid.UUID | None
    payload_json: dict[str, Any] | None
    category: NotificationCategory = NotificationCategory.digest
    group_key: str | None = None
    wake_version: int = 1
    fingerprint_version: NotificationFingerprintVersion = NotificationFingerprintVersion.v2
    event_count: int = 1
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None
    read_at: datetime | None
    created_at: datetime
    updated_at: datetime | None = None


class NotificationListRead(BaseModel):
    items: list[NotificationRead]
    total: int
    unread_count: int


class NotificationDispatchCreate(BaseModel):
    """Body for ``POST /agents/me/notifications/dispatch``.

    Host-orchestrated ``host invoke --timeout`` uses this as the cancellation
    channel: when an invoke times out, the host dispatches a wakeable
    notification to the target persona so it knows its session was orphaned by
    the timeout, rather than silently killing the process.
    """

    recipient_agent_id: uuid.UUID
    event: str = Field(..., max_length=128)
    summary: str = Field(..., max_length=1024)
    target_type: str = "experiment"
    target_id: uuid.UUID | None = None
    payload: dict[str, Any] | None = None
    wakeable: bool = True
