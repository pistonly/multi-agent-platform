"""Advance-round participant acknowledgement helpers."""

from __future__ import annotations

import re
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
from server.services.thread_activity import topic_comment_order_clauses, topic_comment_sort_key

ROUND_SUMMARY_RE = re.compile(r"^##\s*Round\s+\d+\s+Summary\b", re.MULTILINE | re.IGNORECASE)


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
            .order_by(*topic_comment_order_clauses())
        )
    )


def _comments_for_topic(
    db: Session,
    topic_id: uuid.UUID,
    comments: list[TopicComment] | None,
) -> list[TopicComment]:
    """Return preloaded comments when provided; otherwise load from DB."""
    if comments is not None:
        return comments
    return _topic_comments(db, topic_id)


def required_ack_agent_ids(
    db: Session,
    topic: Topic,
    *,
    comments: list[TopicComment] | None = None,
) -> set[uuid.UUID]:
    """Participants who must ack before advance (dynamic set)."""
    required: set[uuid.UUID] = set()
    dismissed: set[uuid.UUID] = set()
    for comment in _comments_for_topic(db, topic.id, comments):
        if comment.author_agent_id == topic.creator_agent_id:
            continue
        kind = _ack_kind(comment.body)
        if kind == "dismiss":
            dismissed.add(comment.author_agent_id)
            continue
        required.add(comment.author_agent_id)
    return required - dismissed


def latest_ack_kind_since(
    comments: list[TopicComment],
    agent_id: uuid.UUID,
    *,
    since: datetime,
) -> str | None:
    """Return the agent's most recent ack kind on or after ``since``."""
    cutoff = _as_utc(since)
    latest_kind: str | None = None
    latest_at: datetime | None = None
    for comment in comments:
        if comment.author_agent_id != agent_id:
            continue
        created_at = _as_utc(comment.created_at)
        if created_at < cutoff:
            continue
        kind = _ack_kind(comment.body)
        if kind is None:
            continue
        if latest_at is None or created_at >= latest_at:
            latest_at = created_at
            latest_kind = kind
    return latest_kind


def acknowledged_agent_ids(
    db: Session,
    topic: Topic,
    *,
    host_ack_ids: list[uuid.UUID],
    comments: list[TopicComment] | None = None,
) -> set[uuid.UUID]:
    acked = set(host_ack_ids)
    loaded = _comments_for_topic(db, topic.id, comments)
    cutoff = _ack_cutoff_for_topic(db, topic, comments=loaded)
    if cutoff is None:
        return acked
    for agent_id in required_ack_agent_ids(db, topic, comments=loaded):
        if latest_ack_kind_since(loaded, agent_id, since=cutoff) == "accept":
            acked.add(agent_id)
    return acked


def reject_agent_ids(
    db: Session,
    topic: Topic,
    *,
    comments: list[TopicComment] | None = None,
) -> set[uuid.UUID]:
    loaded = _comments_for_topic(db, topic.id, comments)
    cutoff = _ack_cutoff_for_topic(db, topic, comments=loaded)
    if cutoff is None:
        return set()
    rejected: set[uuid.UUID] = set()
    for agent_id in required_ack_agent_ids(db, topic, comments=loaded):
        if latest_ack_kind_since(loaded, agent_id, since=cutoff) == "reject":
            rejected.add(agent_id)
    return rejected


def advance_round_ack_state(
    db: Session,
    topic: Topic,
    *,
    now: datetime | None = None,
    comments: list[TopicComment] | None = None,
) -> str:
    """Return ack gate state for the current Round Summary: none|pending|rejected|ready."""
    if topic.archived_at is not None:
        return "none"
    if topic.advance_round_pending_since is None:
        return "none"
    loaded = _comments_for_topic(db, topic.id, comments)
    if _ack_cutoff_for_topic(db, topic, comments=loaded) is None:
        return "none"
    if reject_agent_ids(db, topic, comments=loaded):
        return "rejected"
    required = required_ack_agent_ids(db, topic, comments=loaded)
    if not required:
        return "ready"
    acked = acknowledged_agent_ids(db, topic, host_ack_ids=[], comments=loaded)
    if not (required - acked):
        return "ready"
    if ack_timeout_elapsed(topic, now=now):
        return "ready"
    return "pending"


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


def is_round_summary_comment(body: str) -> bool:
    """Top-level Round N Summary posts use this heading (see topic-host Skill)."""
    return bool(ROUND_SUMMARY_RE.search(body.strip()))


def latest_host_round_summary_comment(
    comments: list[TopicComment],
    *,
    host_agent_id: uuid.UUID,
) -> TopicComment | None:
    candidates = [
        comment
        for comment in comments
        if comment.author_agent_id == host_agent_id
        and comment.parent_comment_id is None
        and (
            getattr(comment, "is_round_summary", False)
            or is_round_summary_comment(comment.body)
        )
    ]
    if not candidates:
        return None
    return max(candidates, key=topic_comment_sort_key)


def _ack_cutoff_for_topic(
    db: Session,
    topic: Topic,
    *,
    comments: list[TopicComment] | None = None,
) -> datetime | None:
    loaded = _comments_for_topic(db, topic.id, comments)
    summary = latest_host_round_summary_comment(loaded, host_agent_id=topic.creator_agent_id)
    if summary is not None:
        return _as_utc(summary.created_at)
    if topic.advance_round_pending_since is not None:
        return _as_utc(topic.advance_round_pending_since)
    return None


def agent_has_round_ack_since(
    comments: list[TopicComment],
    agent_id: uuid.UUID,
    *,
    since: datetime,
) -> bool:
    cutoff = _as_utc(since)
    for comment in comments:
        if comment.author_agent_id != agent_id:
            continue
        if _as_utc(comment.created_at) < cutoff:
            continue
        if _ack_kind(comment.body) in {"accept", "reject", "dismiss"}:
            return True
    return False


def agent_needs_round_ack(
    db: Session,
    topic: Topic,
    agent_id: uuid.UUID,
    *,
    comments: list[TopicComment] | None = None,
) -> bool:
    """True when agent must post --ack accept/reject/dismiss for the current Summary."""
    if agent_id == topic.creator_agent_id:
        return False
    loaded = _comments_for_topic(db, topic.id, comments)
    required = required_ack_agent_ids(db, topic, comments=loaded)
    if agent_id not in required:
        return False
    cutoff = _ack_cutoff_for_topic(db, topic, comments=loaded)
    if cutoff is None:
        return False
    return not agent_has_round_ack_since(loaded, agent_id, since=cutoff)


def mark_round_ack_pending(topic: Topic, *, now: datetime | None = None) -> None:
    topic.advance_round_pending_since = now or datetime.now(UTC)
