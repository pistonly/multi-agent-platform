"""SSE wire-format schemas (transport layer)."""

from __future__ import annotations

from typing import TypedDict

# Fields allowed only on the SSE transport envelope (not domain models).
SSE_TRANSPORT_ONLY_FIELDS = frozenset({"type"})

# Published by notification_service._emit_created via notification_stream.publish.
NOTIFICATION_CREATED_SSE_FIELDS = frozenset({"type", "event", "notification_id"})


class NotificationCreatedSseEvent(TypedDict):
    type: str
    event: str
    notification_id: str
