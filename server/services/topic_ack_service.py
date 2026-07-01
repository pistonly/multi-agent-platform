"""Advance-round participant acknowledgement helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Topic, TopicComment
from server.domain.topic_ack_constants import (
    ACK_ACCEPT_MARKER,
    ACK_DISMISS_MARKER,
    ACK_REJECT_MARKER,
    ADVANCE_ROUND_ACK_TIMEOUT,
)
from server.services.errors import ConflictError


class AdvanceRoundError(ConflictError):
    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def _ack_kind(body: str) -> str | None:
    text = body.strip()
    if ACK_REJECT_MARKER in text:
        return "reject"
    if ACK_DISMISS_MARKER in text:
        return "dismiss"
    if ACK_ACCEPT_MARKER in text:
        return "accept"
    return None


def _topic_comments(db: Session, topic_id: uuid.UUID) -> list[TopicComment]:
    return list(
        db.scalars(
            select(TopicComment)
            .where(TopicComment.topic_id == topic_id)
            .order_by(TopicComment.created_at.asc())
        )
    )


def required_ack_agent_ids(db: Session, topic: Topic) -> set[uuid.UUID]:
    """Participants who must ack before advance (dynamic set)."""
    required: set[uuid.UUID] = set()
    dismissed: set[uuid.UUID] = set()
    for comment in _topic_comments(db, topic.id):
        if comment.author_agent_id == topic.creator_agent_id:
            continue
        kind = _ack_kind(comment.body)
        if kind == "dismiss":
            dismissed.add(comment.author_agent_id)
            continue
        required.add(comment.author_agent_id)
    return required - dismissed


def acknowledged_agent_ids(
    db: Session,
    topic: Topic,
    *,
    host_ack_ids: list[uuid.UUID],
) -> set[uuid.UUID]:
    acked = set(host_ack_ids)
    for comment in _topic_comments(db, topic.id):
        if _ack_kind(comment.body) == "accept":
            acked.add(comment.author_agent_id)
    return acked


def reject_agent_ids(db: Session, topic: Topic) -> set[uuid.UUID]:
    rejected: set[uuid.UUID] = set()
    for comment in _topic_comments(db, topic.id):
        if _ack_kind(comment.body) == "reject":
            rejected.add(comment.author_agent_id)
    return rejected


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def ack_timeout_elapsed(topic: Topic, *, now: datetime | None = None) -> bool:
    if topic.advance_round_pending_since is None:
        return False
    current = _as_utc(now or datetime.now(UTC))
    pending = _as_utc(topic.advance_round_pending_since)
    return current - pending >= ADVANCE_ROUND_ACK_TIMEOUT


def validate_advance_ack(
    db: Session,
    topic: Topic,
    *,
    acknowledged_by: list[uuid.UUID],
    now: datetime | None = None,
) -> None:
    if topic.archived_at is not None:
        raise AdvanceRoundError("Cannot advance an archived topic", reason="archived_topic")

    rejected = reject_agent_ids(db, topic)
    if rejected:
        raise AdvanceRoundError(
            "Participant rejected advancing the discussion round",
            reason="ack_rejected",
        )

    required = required_ack_agent_ids(db, topic)
    if not required:
        topic.advance_round_pending_since = None
        return

    acked = acknowledged_agent_ids(db, topic, host_ack_ids=acknowledged_by)
    missing = required - acked
    if not missing:
        topic.advance_round_pending_since = None
        return

    if ack_timeout_elapsed(topic, now=now):
        topic.advance_round_pending_since = None
        return

    if topic.advance_round_pending_since is None:
        topic.advance_round_pending_since = now or datetime.now(UTC)
    db.commit()
    db.refresh(topic)

    raise AdvanceRoundError(
        "Waiting for participant acknowledgement before advancing round",
        reason="ack_pending",
    )


def participant_ack_body(kind: str) -> str:
    marker = {
        "accept": ACK_ACCEPT_MARKER,
        "reject": ACK_REJECT_MARKER,
        "dismiss": ACK_DISMISS_MARKER,
    }[kind]
    return f"Participant round acknowledgement ({marker})."
