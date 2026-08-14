"""Experiment B / I3: action_item_service mark_wake_sent / mark_stale.

Verifies the transaction-internal helpers that the runtime-waker will use to
advance the three-stage escalation timeline (T+24h → T+72h → 7d×N → stale).

Acceptance mapping (plan §8):
- B-2 / B-3 / B-4: mark_wake_sent bumps wake_count + stamps last_woken_at + emits
  ``action_item.wake_sent`` audit row, all in one transaction.
- B-5: mark_stale stamps stale_at + emits ``action_item.stale`` audit row in
  the same transaction.
- B-6: stale is idempotent — re-marking does not re-write the audit row.
- B-7 contract (no test here, covered in test_action_items): wake_count /
  stale_at survive a subsequent ``complete_action_item``.
- B-9: callers can inject ``now`` so waker / API clock skew cannot drift the
  timeline (plan §4).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from map_types.enums import AgentRole, TopicActionItemStatus, TopicDiscussionRound, TopicStatus
from sqlalchemy import select

from server.domain.models import Agent, AuditLog, Project, Topic, TopicActionItem, TopicDecision
from server.services.action_item_service import (
    WAKE_MAX_COUNT_BEFORE_STALE,
    WAKE_REPEAT_INTERVAL_DAYS,
    WAKE_STAGE_THRESHOLDS,
    mark_stale_no_commit,
    mark_wake_sent_no_commit,
)

# Sentinel for "argument not supplied" so we can distinguish a defaulted field
# from an explicitly-None argument (Python's normal default-argument semantics
# collapse None and "unspecified" together). Same trick I2 uses.
_UNSET: object = object()


def _aware(dt: datetime | None) -> datetime | None:
    """Normalize SQLite's naive datetimes back to UTC-aware for comparisons.

    I1 stored ``first_open_at`` / ``last_woken_at`` / ``stale_at`` as
    ``VARCHAR(64)`` to stay portable between SQLite (dev) and PG (prod).
    SQLAlchemy reads them back as naive ``datetime`` on SQLite because the
    driver can't reconstruct the original tzinfo from a bare ISO string.
    Production PG round-trips with tzinfo intact, so this normalize is only
    needed for SQLite — keep it local to the test file rather than baking it
    into production code.
    """
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _seed_open_item(
    db_session,
    *,
    owner_agent_id=_UNSET,
    first_open_at: datetime | None = None,
    wake_count: int = 0,
    last_woken_at: datetime | None = None,
    stale_at: datetime | None = None,
) -> TopicActionItem:
    """Create a TopicActionItem with the FK chain needed by service helpers.

    ``owner_agent_id`` uses the same sentinel pattern as I2 so a test can
    request an *explicit* unassigned item by passing ``owner_agent_id=None``.
    The default still creates a real assignee Agent.
    """
    project = Project(
        project_key=f"p-{uuid.uuid4().hex[:8]}",
        name="B I3 wake/stale test",
        workspace_path="/tmp/b-i3",
    )
    db_session.add(project)
    db_session.flush()

    creator = Agent(
        name=f"creator-{uuid.uuid4().hex[:8]}",
        api_token_hash="x" * 64,
        role=AgentRole.agent,
        project_id=project.id,
    )
    db_session.add(creator)
    db_session.flush()

    if owner_agent_id is _UNSET:
        assignee = Agent(
            name=f"assignee-{uuid.uuid4().hex[:8]}",
            api_token_hash="x" * 64,
            role=AgentRole.agent,
            project_id=project.id,
        )
        db_session.add(assignee)
        db_session.flush()
        owner_agent_id = assignee.id
    # else: use whatever was passed (UUID or explicit None for unassigned)

    topic = Topic(
        project_id=project.id,
        creator_agent_id=creator.id,
        title="I3 test topic",
        status=TopicStatus.closed,
        discussion_round=TopicDiscussionRound.ready,
    )
    db_session.add(topic)
    db_session.flush()

    decision = TopicDecision(
        project_id=project.id,
        topic_id=topic.id,
        author_agent_id=creator.id,
        decision="d",
    )
    db_session.add(decision)
    db_session.flush()

    item = TopicActionItem(
        decision_id=decision.id,
        project_id=project.id,
        topic_id=topic.id,
        title="I3 test action",
        owner_agent_id=owner_agent_id,
        status=TopicActionItemStatus.open,
        first_open_at=first_open_at,
        wake_count=wake_count,
        last_woken_at=last_woken_at,
        stale_at=stale_at,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


# ---------------------------------------------------------------------------
# mark_wake_sent_no_commit
# ---------------------------------------------------------------------------


def test_mark_wake_sent_first_call_stamps_first_open_at_when_missing(db_session):
    """I3 contract from I1: newly created/reopened items must get first_open_at.

    The seed deliberately leaves ``first_open_at`` None; the helper should
    fill it in on the first wake. Without this, B-2's T+24h window would
    never open.
    """
    when = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    item = _seed_open_item(db_session, first_open_at=None)

    new_count = mark_wake_sent_no_commit(db_session, item=item, now=when)

    assert new_count == 1
    assert item.wake_count == 1
    assert _aware(item.last_woken_at) == when
    assert _aware(item.first_open_at) == when  # stamped on first wake


def test_mark_wake_sent_does_not_reset_first_open_at_on_subsequent_wakes(db_session):
    """Plan §2: timer is anchored to first_open_at; later wakes must not move it."""
    first_open = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    item = _seed_open_item(db_session, first_open_at=first_open, wake_count=1)

    mark_wake_sent_no_commit(
        db_session, item=item, now=datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    )

    assert _aware(item.first_open_at) == first_open  # unchanged
    assert item.wake_count == 2


def test_mark_wake_sent_writes_action_item_wake_sent_audit_row(db_session):
    """B-2 audit signal: wake_count after increment is recorded for grep."""
    when = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    item = _seed_open_item(db_session, wake_count=3)

    new_count = mark_wake_sent_no_commit(db_session, item=item, now=when)
    db_session.commit()

    assert new_count == 4  # == WAKE_MAX_COUNT_BEFORE_STALE
    row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "action_item.wake_sent")
    )
    assert row is not None
    payload = row.payload_json or {}
    assert payload["action_item_id"] == str(item.id)
    assert payload["wake_count"] == 4
    assert _aware(datetime.fromisoformat(payload["last_woken_at"])) == when


def test_mark_wake_sent_advances_through_all_four_stages(db_session):
    """B-2 / B-3 / B-4 walk: 1 → 2 → 3 → 4 across four separate wakes."""
    item = _seed_open_item(db_session, first_open_at=datetime(2026, 6, 1, tzinfo=timezone.utc))

    for expected in (1, 2, 3, 4):
        out = mark_wake_sent_no_commit(
            db_session,
            item=item,
            now=datetime(2026, 7, expected, 12, 0, tzinfo=timezone.utc),
        )
        assert out == expected
        assert item.wake_count == expected

    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "action_item.wake_sent").order_by(AuditLog.created_at)
    ).all()
    assert [r.payload_json["wake_count"] for r in rows] == [1, 2, 3, 4]


def test_mark_wake_sent_rejects_unassigned_item(db_session):
    """Plan §3: waker only wakes owner_agent_id; stale cannot fire for orphans."""
    item = _seed_open_item(db_session, owner_agent_id=None)
    # Sanity: the seed really produced an unassigned item.
    assert item.owner_agent_id is None

    with pytest.raises(ValueError, match="owner_agent_id"):
        mark_wake_sent_no_commit(db_session, item=item)


def test_mark_wake_sent_rejects_non_open_status(db_session):
    """Done / cancelled items must not get a wake bump (B-7 contract)."""
    item = _seed_open_item(db_session)
    item.status = TopicActionItemStatus.done

    with pytest.raises(ValueError, match="status=open"):
        mark_wake_sent_no_commit(db_session, item=item)


def test_mark_wake_sent_no_commit_does_not_commit(db_session):
    """Caller owns the transaction: a no_commit call must not flush-to-disk."""
    item = _seed_open_item(db_session)

    mark_wake_sent_no_commit(db_session, item=item)
    db_session.rollback()  # if helper auto-committed, rollback would be a no-op

    fresh = db_session.get(TopicActionItem, item.id)
    assert fresh.wake_count == 0  # rolled back, mutation did not persist
    assert fresh.last_woken_at is None


# ---------------------------------------------------------------------------
# mark_stale_no_commit
# ---------------------------------------------------------------------------


def test_mark_stale_stamps_stale_at_and_writes_audit(db_session):
    """B-5: stale fires after wake_count >= WAKE_MAX_COUNT_BEFORE_STALE."""
    when = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    last_woken = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    item = _seed_open_item(
        db_session,
        wake_count=WAKE_MAX_COUNT_BEFORE_STALE,
        last_woken_at=last_woken,
    )

    mark_stale_no_commit(db_session, item=item, now=when)
    db_session.commit()

    assert _aware(item.stale_at) == when
    row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "action_item.stale")
    )
    assert row is not None
    payload = row.payload_json or {}
    assert payload["action_item_id"] == str(item.id)
    assert payload["wake_count"] == WAKE_MAX_COUNT_BEFORE_STALE
    assert payload["stale_after_attempt"] == WAKE_MAX_COUNT_BEFORE_STALE
    assert payload["admin_notified"] is True
    assert payload["creator_audit_only"] is True
    assert _aware(datetime.fromisoformat(payload["last_woken_at"])) == last_woken


def test_mark_stale_is_idempotent_does_not_rewrite_audit(db_session):
    """B-6: re-marking an already-stale item is a no-op (no spam audit rows)."""
    stale_ts = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
    item = _seed_open_item(
        db_session,
        wake_count=WAKE_MAX_COUNT_BEFORE_STALE,
        last_woken_at=datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc),
        stale_at=stale_ts,
    )

    mark_stale_no_commit(
        db_session,
        item=item,
        now=datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc),
    )
    db_session.commit()

    # stale_at unchanged (skipped)
    assert _aware(item.stale_at) == stale_ts
    rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "action_item.stale")
    ).all()
    assert rows == []  # no audit row written — helper short-circuited


def test_mark_stale_rejects_unassigned_item(db_session):
    """Mirror of mark_wake_sent's owner_agent_id rejection."""
    item = _seed_open_item(
        db_session,
        owner_agent_id=None,
        wake_count=4,
        last_woken_at=datetime.now(timezone.utc),
    )
    with pytest.raises(ValueError, match="owner_agent_id"):
        mark_stale_no_commit(db_session, item=item)


def test_mark_stale_rejects_when_no_prior_wake(db_session):
    """Plan §1: stale fires only after at least one wake."""
    item = _seed_open_item(
        db_session,
        wake_count=4,  # count == 4 but last_woken_at is None (inconsistent)
        last_woken_at=None,
    )
    with pytest.raises(ValueError, match="last_woken_at"):
        mark_stale_no_commit(db_session, item=item)


def test_mark_stale_rejects_non_open_status(db_session):
    """Stale is for open items — done/cancelled items never reach stale."""
    item = _seed_open_item(
        db_session,
        wake_count=4,
        last_woken_at=datetime.now(timezone.utc),
    )
    item.status = TopicActionItemStatus.cancelled

    with pytest.raises(ValueError, match="status=open"):
        mark_stale_no_commit(db_session, item=item)


# ---------------------------------------------------------------------------
# Wake + stale transition (the realistic waker path)
# ---------------------------------------------------------------------------


def test_full_wake_to_stale_lifecycle(db_session):
    """End-to-end: T+24h → T+72h → 7d → 7d → stale.

    Mirrors the plan §3 should_wake_action_item state machine, exercised via
    the service helpers without mocking time-of-day — callers pass ``now``
    so the test is deterministic.
    """
    first_open = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    item = _seed_open_item(db_session, first_open_at=first_open)

    # T+24h: first wake
    c = mark_wake_sent_no_commit(
        db_session, item=item, now=first_open + timedelta(hours=24)
    )
    assert c == 1
    # T+72h: second wake
    c = mark_wake_sent_no_commit(
        db_session, item=item, now=first_open + timedelta(hours=72)
    )
    assert c == 2
    # 7d after the 2nd wake: third wake
    third = first_open + timedelta(hours=72) + timedelta(days=WAKE_REPEAT_INTERVAL_DAYS)
    c = mark_wake_sent_no_commit(db_session, item=item, now=third)
    assert c == 3
    # 7d later: fourth wake
    fourth = third + timedelta(days=WAKE_REPEAT_INTERVAL_DAYS)
    c = mark_wake_sent_no_commit(db_session, item=item, now=fourth)
    assert c == WAKE_MAX_COUNT_BEFORE_STALE
    # 7d after the 4th wake: no fifth wake — stale instead
    stale_when = fourth + timedelta(days=WAKE_REPEAT_INTERVAL_DAYS)
    mark_stale_no_commit(db_session, item=item, now=stale_when)
    db_session.commit()

    assert item.wake_count == WAKE_MAX_COUNT_BEFORE_STALE
    assert _aware(item.stale_at) == stale_when
    # audit timeline: 4 wake_sent + 1 stale = 5 rows
    wake_rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "action_item.wake_sent")
    ).all()
    stale_rows = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "action_item.stale")
    ).all()
    assert len(wake_rows) == 4
    assert len(stale_rows) == 1
    # wake_count ordering matches the timeline
    assert [r.payload_json["wake_count"] for r in wake_rows] == [1, 2, 3, 4]


def test_threshold_constants_match_plan_section_three():
    """Sanity guard: bumping the waker schedule must update both sides.

    Plan §3 lists: T+24h, T+72h, then 7d × up to 4. If anyone changes
    WAKE_STAGE_THRESHOLDS or WAKE_MAX_COUNT_BEFORE_STALE they have to
    consciously update this test, not silently drift.
    """
    assert WAKE_STAGE_THRESHOLDS == ((1, 24), (2, 72))
    assert WAKE_MAX_COUNT_BEFORE_STALE == 4
    assert WAKE_REPEAT_INTERVAL_DAYS == 7
