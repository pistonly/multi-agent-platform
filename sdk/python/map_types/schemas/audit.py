"""Audit log schemas and action-item wake payload constants."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .base import ORMModel



# --- Audit ---


class AuditLogRead(ORMModel):
    id: uuid.UUID
    agent_id: uuid.UUID | None
    project_id: uuid.UUID | None
    action: str
    target_type: str
    target_id: uuid.UUID | None
    summary: str | None
    payload_json: dict | None
    created_at: datetime


# 0db51e10 I2(5e): request body for ``POST /experiments/{id}/cross-persona-call``.
# Persisted in audit_logs.payload_json alongside caller_agent_id /
# target_experiment_id / timestamp (created_at column).
class CrossPersonaCallRecord(BaseModel):
    visibility_diff: dict[str, Any] = Field(default_factory=dict)
    result_partition_count: int = Field(default=0, ge=0)
    diff_size: int = Field(default=0, ge=0)


# --- Action Item audit payloads (B I2: waker escalation 三段式) ---


ACTION_ITEM_WAKE_SENT = "action_item.wake_sent"
ACTION_ITEM_STALE = "action_item.stale"


class ActionItemWakeSentPayload(BaseModel):
    """Payload of ``action_item.wake_sent`` audit event.

    Fired by ``runtime-waker.scan_pending_action_items`` whenever ``should_wake_action_item``
    decides to wake the assignee (T+24h / T+72h / every 7d up to 4 times). Pairs with the
    ``action_item.stale`` event but is a distinct, lower-severity audit signal.
    """

    action_item_id: uuid.UUID
    owner_agent_id: uuid.UUID
    topic_id: uuid.UUID
    decision_id: uuid.UUID | None = None
    linked_experiment_id: uuid.UUID | None = None
    wake_count: int
    last_woken_at: datetime
    first_open_at: datetime
    elapsed_since_first_open_seconds: int
    triggered_by: str = "waker.scan_pending_action_items"


class ActionItemStalePayload(BaseModel):
    """Payload of ``action_item.stale`` audit event.

    Fired after the 4th unanswered wake at the next 7d boundary. Pairs with admin
    notification + creator audit-only mark; the runtime-waker stops waking the
    assignee once ``stale_at`` is set. The event itself is independent of any
    state transition (unlike ``action_item.completed`` / ``action_item.cancelled``):
    it is a diagnostic signal that the open item has not progressed.
    """

    action_item_id: uuid.UUID
    owner_agent_id: uuid.UUID
    topic_id: uuid.UUID
    decision_id: uuid.UUID | None = None
    linked_experiment_id: uuid.UUID | None = None
    last_woken_at: datetime | None
    wake_count: int
    stale_after_attempt: int
    admin_notified: bool
    creator_audit_only: bool
