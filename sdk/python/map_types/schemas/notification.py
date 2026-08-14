"""Notification schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel

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
    payload_json: dict | None
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
