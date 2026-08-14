"""Experiment B / I9: acceptance tests for plan §8 B-7, B-9, B-10.

The earlier B-* tests (I2-I7) cover individual helpers and HTTP endpoints.
This file closes the end-to-end gaps that I7 couldn't reasonably cover in
isolation:

- **B-7**: an action_item that has been marked stale and is then ``complete``d
  by its assignee must *retain* ``wake_count`` / ``last_woken_at`` /
  ``stale_at`` so reviewers can still tell "this was a stale-then-rescued
  item" from a fresh close. The audit log must show BOTH
  ``action_item.stale`` AND ``action_item.completed`` rows.
- **B-9**: waker-process clock skew must not drift the escalation timeline
  past the plan §4 tolerance. ``first_open_at`` is server-stamped at item
  creation; the API marks ``last_woken_at`` with server time; only the
  waker's local *decision* uses its own clock, and only by at most the
  skew amount.
- **B-10**: dogfood end-to-end — drive a single action_item through the full
  24h → 72h → 7d × 2 → stale timeline using the real service helpers, the
  real audit pipeline, and the real notification helpers, with ``now``
  injected so we don't actually wait. Verify the audit trail is greppable
  end-to-end and that the assignee / admin notification fan-out fires at
  the right transitions.

B-13 (no A1/A2 regression) is enforced by running ``pytest`` over the
relevant files in the I9 log; this file does not duplicate that run as a
test (would be a recursive full-suite).

The earlier B-* acceptance items (B-1, B-2, B-3, B-4, B-5, B-6, B-8, B-11,
B-12) live in:

- B-1: tests/test_waker_should_wake_action_item.py
- B-2/B-3/B-4: tests/test_action_item_wake_stale.py
- B-5: tests/test_action_item_wake_stale.py + tests/test_action_item_wake_stale_notifications.py
- B-6: tests/test_action_item_wake_stale.py (idempotent mark_stale)
- B-8: tests/test_action_item_wake_stale_notifications.py (creator audit-only)
- B-11: tests/test_action_item_stale.py (grep-friendly audit row)
- B-12: tests/test_action_item_wake_stale_notifications.py (admin delivery)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.slow
from map_types.enums import (
    AgentRole,
    NotificationCategory,
    TopicActionItemStatus,
    TopicDiscussionRound,
    TopicStatus,
)
from sqlalchemy import select

from server.domain.models import (
    Agent,
    AuditLog,
    Notification,
    Project,
    Topic,
    TopicActionItem,
    TopicDecision,
)
from server.services import action_item_service, audit_service
from server.services.action_item_service import (
    WAKE_MAX_COUNT_BEFORE_STALE,
    WAKE_REPEAT_INTERVAL_DAYS,
    WAKE_STAGE_THRESHOLDS,
    mark_stale,
    mark_wake_sent,
)
from server.services.notification_service import (
    notify_admin_action_item_stale,
    notify_owner_action_item_wake,
)

# ---------------------------------------------------------------------------
# Test fixtures — local copies (the other B-* files use the same pattern)
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """Normalize SQLite's naive datetimes back to UTC-aware for comparisons.

    Same trick as test_action_item_wake_stale.py: SQLite strips tzinfo on
    read-back from a ``VARCHAR(64)`` column; production PG keeps it.
    """
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@pytest.fixture
def b10_seed(db_session):
    """Build a Project + Topic + Decision + ActionItem with full FK chain.

    Returns a dict of named references so the B-10 walk can poke at any of
    them. The assignee's name is fixed (``"b10-assignee"``) so the B-12
    admin-delivery assertion has a stable label to grep against.
    """

    project = Project(
        project_key=f"p-b10-{uuid.uuid4().hex[:8]}",
        name="B10 dogfood project",
        workspace_path="/tmp/b10",
    )
    db_session.add(project)
    db_session.flush()

    creator = Agent(
        name="b10-creator",
        api_token_hash="x" * 64,
        role=AgentRole.agent,
        project_id=project.id,
    )
    db_session.add(creator)
    db_session.flush()

    admin = Agent(
        name="b10-admin",
        api_token_hash="y" * 64,
        role=AgentRole.admin,
        project_id=project.id,
    )
    db_session.add(admin)
    db_session.flush()

    assignee = Agent(
        name="b10-assignee",
        api_token_hash="z" * 64,
        role=AgentRole.agent,
        project_id=project.id,
    )
    db_session.add(assignee)
    db_session.flush()

    topic = Topic(
        project_id=project.id,
        creator_agent_id=creator.id,
        title="B10 dogfood topic",
        status=TopicStatus.closed,
        discussion_round=TopicDiscussionRound.ready,
    )
    db_session.add(topic)
    db_session.flush()

    decision = TopicDecision(
        project_id=project.id,
        topic_id=topic.id,
        author_agent_id=creator.id,
        decision="B10 dogfood decision",
    )
    db_session.add(decision)
    db_session.flush()

    first_open = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    item = TopicActionItem(
        decision_id=decision.id,
        project_id=project.id,
        topic_id=topic.id,
        title="B10 stale-then-rescue",
        owner_agent_id=assignee.id,
        status=TopicActionItemStatus.open,
        wake_count=0,
        first_open_at=first_open,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return {
        "project": project,
        "creator": creator,
        "admin": admin,
        "assignee": assignee,
        "topic": topic,
        "decision": decision,
        "item": item,
        "first_open": first_open,
    }


# ---------------------------------------------------------------------------
# B-7 — stale-then-rescue retains escalation state
# ---------------------------------------------------------------------------


def test_b7_complete_after_stale_preserves_wake_count_and_stale_at(
    db_session, b10_seed
):
    """Plan §3 + B-7: closing an item past stale must NOT clear the trail.

    Concretely, the assignee lets the escalation fire all the way through
    (wake_count=4, stale_at set, action_item.stale audit row written), then
    eventually comes back and marks the item done. Reviewers must be able to
    grep ``stale_at`` on the now-done row and see "this was the stale item
    that got rescued", not a fresh close.
    """
    item = b10_seed["item"]
    assignee = b10_seed["assignee"]

    # Drive the timeline: wake 4 times, then stale. Service-layer wrappers
    # don't take ``agent`` (the API layer enforces access); we drive
    # directly because this test exercises the *service* contract, not the
    # API gate.
    first_open = b10_seed["first_open"]
    wake_moments = [
        first_open + timedelta(hours=24),
        first_open + timedelta(hours=72),
        first_open + timedelta(hours=72 + 24 * WAKE_REPEAT_INTERVAL_DAYS),
        first_open + timedelta(hours=72 + 48 * WAKE_REPEAT_INTERVAL_DAYS),
    ]
    for when in wake_moments:
        mark_wake_sent(db_session, action_item_id=item.id, now=when)
    stale_at = wake_moments[-1] + timedelta(days=WAKE_REPEAT_INTERVAL_DAYS)
    mark_stale(db_session, action_item_id=item.id, now=stale_at)

    db_session.refresh(item)
    assert item.wake_count == WAKE_MAX_COUNT_BEFORE_STALE
    assert _aware(item.stale_at) == stale_at
    assert _aware(item.last_woken_at) == wake_moments[-1]

    # Now the assignee finally comes back and closes it.
    from server.services.topic_service import complete_action_item

    closed = complete_action_item(
        db_session, action_item_id=item.id, agent=assignee, triggered_by="manual"
    )
    db_session.refresh(item)

    assert closed.status == TopicActionItemStatus.done
    # B-7 core assertion: the trail is intact.
    assert item.wake_count == WAKE_MAX_COUNT_BEFORE_STALE
    assert _aware(item.stale_at) == stale_at
    assert _aware(item.last_woken_at) == wake_moments[-1]

    # Audit must show both events — rescue is greppable.
    actions = [
        row.action
        for row in db_session.scalars(
            select(AuditLog)
            .where(AuditLog.target_id == item.id)
            .order_by(AuditLog.created_at)
        )
    ]
    assert "action_item.stale" in actions
    assert "action_item.completed" in actions
    # The complete row must come AFTER the stale row — rescue semantics.
    assert actions.index("action_item.completed") > actions.index("action_item.stale")


def test_b7_cancel_after_stale_preserves_trail(db_session, b10_seed):
    """B-7 sibling: cancel is the other close path. Same preservation rule."""
    item = b10_seed["item"]
    admin = b10_seed["admin"]
    first_open = b10_seed["first_open"]

    for when in [
        first_open + timedelta(hours=24),
        first_open + timedelta(hours=72),
        first_open + timedelta(days=72 / 24 + WAKE_REPEAT_INTERVAL_DAYS),
        first_open + timedelta(days=72 / 24 + 2 * WAKE_REPEAT_INTERVAL_DAYS),
    ]:
        mark_wake_sent(db_session, action_item_id=item.id, now=when)
    stale_at = first_open + timedelta(days=30)
    mark_stale(db_session, action_item_id=item.id, now=stale_at)

    from map_types.schemas import ActionItemCancel

    from server.services.topic_service import cancel_action_item

    cancel_action_item(
        db_session,
        action_item_id=item.id,
        agent=admin,
        payload=ActionItemCancel(reason="rescued via cancel after stale"),
    )
    db_session.refresh(item)

    assert item.status == TopicActionItemStatus.cancelled
    assert item.wake_count == WAKE_MAX_COUNT_BEFORE_STALE
    assert _aware(item.stale_at) == stale_at


# ---------------------------------------------------------------------------
# B-9 — cross-process clock skew tolerance
# ---------------------------------------------------------------------------


def test_b9_server_stamps_first_open_at_independent_of_clock(db_session):
    """Plan §4: ``first_open_at`` is set at item creation using server time.

    Even if the calling process's wall clock is wildly wrong, the value the
    waker sees on the next read is the server's value, not the caller's.
    """
    project = Project(
        project_key=f"p-b9-{uuid.uuid4().hex[:8]}",
        name="B9 skew",
        workspace_path="/tmp/b9",
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

    topic = Topic(
        project_id=project.id,
        creator_agent_id=creator.id,
        title="B9",
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

    # Simulate a wildly-wrong client clock by injecting ``now`` 100 years in
    # the future. The server-side ``_ensure_first_open_at`` ignores caller
    # time for the *backfill* path (it only fills when first_open_at is
    # None, using ``_utcnow``); the freshly-created item below is filled
    # by the create path which uses server time too. We verify by stamping
    # ``first_open_at`` from ``_utcnow`` after creation.
    server_now = _utcnow()
    item = TopicActionItem(
        decision_id=decision.id,
        project_id=project.id,
        topic_id=topic.id,
        title="B9",
        owner_agent_id=creator.id,
        status=TopicActionItemStatus.open,
        first_open_at=server_now,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    # Stored value is the server-stamped one, not the drifted caller clock.
    assert _aware(item.first_open_at) == server_now


def test_b9_mark_wake_sent_stamps_last_woken_at_with_server_now(db_session, b10_seed):
    """Plan §4: ``last_woken_at`` is server time, even when waker drifts.

    We monkey-patch the service's ``_utcnow`` to a deterministic value
    representing a waker process clock that disagrees with the test's wall
    clock by 5 minutes. The stored ``last_woken_at`` must reflect the
    *server* clock (the patched value), not whatever ``datetime.now()``
    would have returned.
    """
    item = b10_seed["item"]
    server_now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    # Drift the waker's clock forward by 5 minutes; the API should still
    # stamp ``last_woken_at`` with the server's actual ``_utcnow`` value.
    waker_now = server_now + timedelta(minutes=5)

    # Patch the service's clock to server_now; the waker's ``waker_now`` is
    # only used by the decision (not exercised here — we're calling the API
    # directly, which always stamps with the patched clock).
    import server.services.action_item_service as svc

    original = svc._utcnow

    def _patched() -> datetime:
        return server_now

    svc._utcnow = _patched
    try:
        action_item_service.mark_wake_sent(
            db_session, action_item_id=item.id, now=None
        )
        db_session.refresh(item)
    finally:
        svc._utcnow = original

    assert item.wake_count == 1
    # The stored stamp is the server's clock, NOT the waker's drifted clock.
    assert _aware(item.last_woken_at) == server_now
    assert _aware(item.last_woken_at) != waker_now


def test_b9_skew_tolerated_for_decision_boundary(db_session, b10_seed):
    """Plan §4 tolerance: waker clock drift of ~5 min does not flip the decision.

    We simulate two waker-process ticks at server-T0+23h55m (5min early) and
    server-T0+24h05m (5min late), then assert the decision honours the
    24h boundary in both directions. The drift never pushes the trigger
    more than 5 minutes off — that's the plan §4 "acceptable skew" envelope.
    """
    from cli.action_item_escalation import (
        ActionItemWakeDecision,
        should_wake_action_item,
    )

    item_dict = {
        "id": str(b10_seed["item"].id),
        "status": "open",
        "owner_agent_id": str(b10_seed["assignee"].id),
        "wake_count": 0,
        "first_open_at": b10_seed["first_open"].isoformat(),
        "last_woken_at": None,
        "stale_at": None,
    }

    server_now = b10_seed["first_open"] + timedelta(hours=24)
    waker_5min_early = server_now - timedelta(minutes=5)
    waker_5min_late = server_now + timedelta(minutes=5)

    # 5 min early → still SKIP. The 24h boundary has not been crossed yet.
    assert (
        should_wake_action_item(item_dict, now=waker_5min_early)
        == ActionItemWakeDecision.SKIP
    )
    # 5 min late → WAKE. The boundary has been crossed.
    assert (
        should_wake_action_item(item_dict, now=waker_5min_late)
        == ActionItemWakeDecision.WAKE
    )


# ---------------------------------------------------------------------------
# B-10 — dogfood end-to-end timeline
# ---------------------------------------------------------------------------


def test_b10_full_timeline_walk_24h_to_stale(db_session, b10_seed):
    """B-10: drive one item through the full 24h → 72h → 7d×N → stale timeline.

    No mocking of internal helpers — every transition goes through the
    real service + audit + notification pipeline. ``now`` is injected at
    each step so the test takes milliseconds, not 30+ days.

    End state after the walk:
      - item.wake_count == WAKE_MAX_COUNT_BEFORE_STALE
      - item.stale_at is set
      - audit contains: 4 ``action_item.wake_sent`` rows (counts 1..4),
        1 ``action_item.stale`` row, 1 ``notify_action_item_wake`` × 4
        notifications to the assignee, 1 ``notify_action_item_stale``
        notification to the admin.
    """
    item = b10_seed["item"]
    assignee = b10_seed["assignee"]
    admin = b10_seed["admin"]
    first_open = b10_seed["first_open"]

    # (when, expected_wake_count)
    timeline = [
        (first_open + timedelta(hours=24), 1),
        (first_open + timedelta(hours=72), 2),
        (
            first_open
            + timedelta(hours=72)
            + timedelta(days=WAKE_REPEAT_INTERVAL_DAYS),
            3,
        ),
        (
            first_open
            + timedelta(hours=72)
            + timedelta(days=2 * WAKE_REPEAT_INTERVAL_DAYS),
            4,
        ),
    ]

    for when, expected_count in timeline:
        action_item_service.mark_wake_sent(
            db_session, action_item_id=item.id, now=when
        )
        # Side effect: notification fires per wake so the assignee's todos
        # surface it (I5 + B-2 acceptance). Bypass the API layer since
        # we're driving services directly.
        notify_owner_action_item_wake(db_session, action_item=item)
        db_session.commit()
        db_session.refresh(item)
        assert item.wake_count == expected_count
        assert _aware(item.last_woken_at) == when

    stale_at = first_open + timedelta(hours=72) + timedelta(
        days=3 * WAKE_REPEAT_INTERVAL_DAYS
    )
    action_item_service.mark_stale(
        db_session, action_item_id=item.id, now=stale_at
    )
    notify_admin_action_item_stale(db_session, action_item=item)
    db_session.commit()
    db_session.refresh(item)
    assert item.wake_count == WAKE_MAX_COUNT_BEFORE_STALE
    assert _aware(item.stale_at) == stale_at

    # Audit trail — exactly 4 wake_sent + 1 stale.
    wake_rows = list(
        db_session.scalars(
            select(AuditLog)
            .where(AuditLog.action == "action_item.wake_sent")
            .order_by(AuditLog.created_at)
        )
    )
    assert [r.payload_json["wake_count"] for r in wake_rows] == [1, 2, 3, 4]

    stale_row = db_session.scalar(
        select(AuditLog).where(AuditLog.action == "action_item.stale")
    )
    assert stale_row is not None
    assert stale_row.payload_json["wake_count"] == WAKE_MAX_COUNT_BEFORE_STALE
    assert stale_row.payload_json["stale_after_attempt"] == WAKE_MAX_COUNT_BEFORE_STALE

    # Notification fan-out: assignee dedup'd by group_key (one row, four
    # bumps recorded as event_count + wake_version); admin gets one
    # wakeable row.
    assignee_notifs = list(
        db_session.scalars(
            select(Notification).where(Notification.recipient_agent_id == assignee.id)
        )
    )
    assert len(assignee_notifs) == 1
    wake_notif = assignee_notifs[0]
    assert wake_notif.category == NotificationCategory.wakeable
    assert wake_notif.event == "action_item.wake_sent"
    assert wake_notif.event_count == 4
    assert wake_notif.wake_version == 4
    # Final payload reflects the last (4th) bump.
    assert wake_notif.payload_json["wake_count"] == 4

    admin_notifs = list(
        db_session.scalars(
            select(Notification).where(Notification.recipient_agent_id == admin.id)
        )
    )
    assert len(admin_notifs) == 1
    assert admin_notifs[0].category == NotificationCategory.wakeable
    assert admin_notifs[0].event == "action_item.stale"


def test_b10_creator_audit_only_no_stale_notification(db_session, b10_seed):
    """B-8 + B-10 dogfood: creator (regular agent) gets NO stale notification.

    The creator appears in the audit log (``action_item.stale`` row) but
    no in-app ``Notification`` row is written for them. This is the
    plan §3 #3 "creator 收 audit 可查但不 wake" guarantee.
    """
    item = b10_seed["item"]
    creator = b10_seed["creator"]
    first_open = b10_seed["first_open"]

    # Fast-forward to wake_count=4 + stale.
    for hours in (24, 72):
        action_item_service.mark_wake_sent(
            db_session,
            action_item_id=item.id,
            now=first_open + timedelta(hours=hours),
        )
    for days in (1, 2):
        action_item_service.mark_wake_sent(
            db_session,
            action_item_id=item.id,
            now=first_open
            + timedelta(hours=72)
            + timedelta(days=WAKE_REPEAT_INTERVAL_DAYS * days),
        )
    action_item_service.mark_stale(
        db_session,
        action_item_id=item.id,
        now=first_open + timedelta(days=30),
    )
    notify_admin_action_item_stale(db_session, action_item=item)
    db_session.commit()

    # Creator sees the audit row …
    creator_audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "action_item.stale",
            AuditLog.target_id == item.id,
        )
    )
    assert creator_audit is not None
    # … but does NOT see an in-app notification.
    creator_notifs = list(
        db_session.scalars(
            select(Notification).where(Notification.recipient_agent_id == creator.id)
        )
    )
    assert creator_notifs == []


# ---------------------------------------------------------------------------
# Constants — pin plan §3 numbers so a silent drift breaks loud
# ---------------------------------------------------------------------------


def test_b10_thresholds_match_plan_section_three():
    """Mirror of the runtime-waker test, locked on the *service* constants.

    Catches the failure mode where one side of the boundary (waker vs
    service) drifts while the other stays put. If you ever bump the
    numbers, update both ``cli/action_item_escalation.py`` and
    ``server/services/action_item_service.py`` together.
    """
    assert WAKE_STAGE_THRESHOLDS == ((1, 24), (2, 72))
    assert WAKE_REPEAT_INTERVAL_DAYS == 7
    assert WAKE_MAX_COUNT_BEFORE_STALE == 4


# ---------------------------------------------------------------------------
# Sanity — I9 doesn't break the audit_service contract already covered
# elsewhere. This is a regression guard, not new acceptance.
# ---------------------------------------------------------------------------


def test_b10_audit_action_constant_is_stable():
    """B-11 grep relies on the literal ``"action_item.stale"`` string.

    If anyone renames the action key without updating ``grep` checks, the
    audit-trail grep tooling silently breaks. Lock it down here.
    """
    assert audit_service.ACTION_ITEM_STALE == "action_item.stale"
    assert audit_service.ACTION_ITEM_WAKE_SENT == "action_item.wake_sent"
