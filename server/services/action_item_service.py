"""Action-item wake / stale lifecycle helpers (experiment B, plan §3).

This module owns the *transaction-internal* mutations that the runtime-waker
needs to advance the three-stage escalation timeline:

- ``mark_wake_sent_no_commit``: bump ``wake_count``, stamp ``last_woken_at``,
  emit the ``action_item.wake_sent`` audit row — all in the caller's tx.
- ``mark_stale_no_commit``: stamp ``stale_at``, emit the ``action_item.stale``
  audit row — all in the caller's tx.

Public ``mark_wake_sent`` / ``mark_stale`` wrappers add lookup + commit for
direct callers (API, dogfood scripts). The ``_no_commit`` variants are what
the waker should call from its own session.

Why a separate service module instead of inlining into ``topic_service.py``:
the waker has its own import surface (no FastAPI deps, no project_status
recursion) and the topic module is already 700+ lines. Splitting keeps the
mutation contract co-located with its audit helpers and the I3 acceptance
tests.

Related plan sections:
- §1  audit event ``action_item.stale`` payload schema
- §2  ``first_open_at`` / ``last_woken_at`` / ``stale_at`` lifecycle
- §3  waker scan / stale trigger
- §4  clock-skew policy: every timestamp is server time, never client time
- §8  B-1..B-13 acceptance — these helpers back B-2..B-7, B-11
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from server.domain.models import TopicActionItem
from server.services import audit_service

# Wake-history window from plan §3. Mirrored here so service-level callers
# (admin scripts, dogfood) can introspect the same numbers the waker uses
# without re-parsing the plan markdown.
WAKE_STAGE_THRESHOLDS: tuple[tuple[int, int], ...] = (
    # (wake_count_after_increment, minimum_hours_since_first_open)
    (1, 24),   # T+24h  first wake
    (2, 72),   # T+72h  second wake
)
WAKE_REPEAT_INTERVAL_DAYS = 7  # after the 72h wake, fire every 7d
WAKE_MAX_COUNT_BEFORE_STALE = 4  # 4th unanswered wake -> stale


def _utcnow() -> datetime:
    """Return the waker-canonical ``datetime.now(UTC)``.

    Centralised so tests can monkey-patch the service module rather than
    chasing ``datetime.now`` through every call site (plan §4 clock-skew
    requirement: only server time, never client time).
    """
    return datetime.now(UTC)


def _ensure_first_open_at(item: TopicActionItem, now: datetime) -> None:
    """Stamp ``first_open_at`` on the first transition into ``open``.

    Per plan §2: ``first_open_at`` is written when an item enters the ``open``
    status (creation, or reopen from ``done``/``cancelled``). Re-triggering
    a wake on an already-open item must NOT reset this — the T+24h / T+72h
    window is anchored to the moment the item first became open.

    The backfill for already-open rows is handled by alembic migration
    ``027_topic_action_item_escalation_fields.py`` — this helper only sets
    the timestamp when it's still null.
    """
    if item.status.value == "open" and item.first_open_at is None:
        item.first_open_at = now


def mark_wake_sent_no_commit(
    db: Session,
    *,
    item: TopicActionItem,
    now: datetime | None = None,
) -> int:
    """Bump ``wake_count`` + stamp ``last_woken_at`` + audit row, no commit.

    Caller is responsible for the final ``db.commit()``. Returns the new
    ``wake_count`` so the waker can decide whether to advance to ``stale``
    on the next cycle (returns 1..4; values beyond ``WAKE_MAX_COUNT_BEFORE_STALE``
    mean the waker is calling this too aggressively and the caller should
    pivot to ``mark_stale_no_commit`` instead).

    Invariants (plan §3 + §4):
      - Only fires when ``status == open`` and ``owner_agent_id`` is set;
        ``wake_count`` is meaningless on closed items.
      - ``last_woken_at`` and the audit row's ``last_woken_at`` field both
        use the same ``now`` value (caller-supplied or server-time default)
        so grep-side reconstruction matches the DB.
      - First call also writes ``first_open_at`` if it's still null — this
        covers the I1 contract that backfill only handles pre-existing rows.
    """
    if item.status.value != "open":
        raise ValueError(
            f"mark_wake_sent requires status=open, got {item.status.value}"
        )
    if item.owner_agent_id is None:
        raise ValueError(
            "mark_wake_sent requires owner_agent_id — "
            "waker only wakes the assignee"
        )

    when = now or _utcnow()
    _ensure_first_open_at(item, when)
    item.wake_count = (item.wake_count or 0) + 1
    item.last_woken_at = when

    audit_service.log_action_item_wake_sent(db, item=item, now=when)
    return item.wake_count


def mark_stale_no_commit(
    db: Session,
    *,
    item: TopicActionItem,
    now: datetime | None = None,
    admin_notified: bool = True,
    creator_audit_only: bool = True,
) -> None:
    """Stamp ``stale_at`` + write the diagnostic audit row, no commit.

    Idempotent: if ``stale_at`` is already set we skip re-writing (the audit
    row is a one-shot diagnostic — repeat wakes after stale must not spam
    audit). Caller still owns the commit boundary.

    Validation is delegated to ``audit_service._log_action_item_stale_no_commit``
    which rejects unassigned items, items with no prior wake, and
    ``stale_after_attempt < 1``. We pass ``stale_after_attempt=item.wake_count``
    because the plan defines stale as "the 4th unanswered wake" — the count
    after the increment.
    """
    if item.status.value != "open":
        raise ValueError(
            f"mark_stale requires status=open, got {item.status.value}"
        )

    if item.stale_at is not None:
        return  # already stale — plan §5: do not re-write diagnostic

    when = now or _utcnow()
    item.stale_at = when

    audit_service._log_action_item_stale_no_commit(
        db,
        item=item,
        stale_after_attempt=item.wake_count,
        admin_notified=admin_notified,
        creator_audit_only=creator_audit_only,
    )


def mark_wake_sent(
    db: Session,
    action_item_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> TopicActionItem:
    """Public wrapper: lookup + ``mark_wake_sent_no_commit`` + commit.

    Kept for API symmetry with ``complete_action_item`` / ``cancel_action_item``.
    The waker itself should call the ``_no_commit`` variant directly.
    """
    item = db.get(TopicActionItem, action_item_id)
    if item is None:
        raise LookupError(f"action item {action_item_id} not found")
    mark_wake_sent_no_commit(db, item=item, now=now)
    db.commit()
    db.refresh(item)
    return item


def mark_stale(
    db: Session,
    action_item_id: uuid.UUID,
    *,
    now: datetime | None = None,
    admin_notified: bool = True,
    creator_audit_only: bool = True,
) -> TopicActionItem:
    """Public wrapper: lookup + ``mark_stale_no_commit`` + commit."""
    item = db.get(TopicActionItem, action_item_id)
    if item is None:
        raise LookupError(f"action item {action_item_id} not found")
    mark_stale_no_commit(
        db,
        item=item,
        now=now,
        admin_notified=admin_notified,
        creator_audit_only=creator_audit_only,
    )
    db.commit()
    db.refresh(item)
    return item
