"""Experiment B / I4: ``should_wake_action_item`` decision unit tests.

Covers every branch of the pure decision function documented in
``cli.runtime_waker.should_wake_action_item`` (plan §3 escalation timeline):

- WAKE at T+24h (wake_count 0 → 1)
- SKIP between T+0 and T+24h
- WAKE at T+72h (wake_count 1 → 2)
- SKIP between T+24h and T+72h (and after T+72h if wake_count not yet at 2)
- WAKE at every 7d after the 72h wake (wake_count 2 → 3, 3 → 4)
- STALE at +7d after the 4th unanswered wake (wake_count >= 4)
- Closed / unassigned / already-stale items always SKIP
- ``scan_pending_action_items`` only returns items owned by the persona

The function is pure (no I/O, no clock) so we inject ``now`` directly and
assert the decision. ``scan_pending_action_items`` is exercised via the
same fixtures because the persona filter is the only thing it adds.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cli.action_item_escalation import (
    ActionItemWakeDecision,
    scan_pending_action_items,
    should_wake_action_item,
)

NOW = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)
OWNER = "8ab78cfc-8289-442e-ae63-fa0df4d2cd68"
OTHER = "98b23700-8386-4def-8235-afe252c1df35"


def _item(**overrides):
    base = {
        "id": "d780a339-99a1-4893-b383-d29f8ceb87b4",
        "status": "open",
        "owner_agent_id": OWNER,
        "wake_count": 0,
        "first_open_at": (NOW - timedelta(hours=1)).isoformat(),
        "last_woken_at": None,
        "stale_at": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Stage 1 — T+24h
# ---------------------------------------------------------------------------


def test_wake_at_exactly_t24h():
    item = _item(wake_count=0, first_open_at=(NOW - timedelta(hours=24)).isoformat())
    assert should_wake_action_item(item, now=NOW) == ActionItemWakeDecision.WAKE


def test_wake_past_t24h_with_no_prior_wake():
    item = _item(wake_count=0, first_open_at=(NOW - timedelta(hours=30)).isoformat())
    assert should_wake_action_item(item, now=NOW) == ActionItemWakeDecision.WAKE


def test_skip_before_t24h():
    item = _item(wake_count=0, first_open_at=(NOW - timedelta(hours=23, minutes=59)).isoformat())
    assert should_wake_action_item(item, now=NOW) == ActionItemWakeDecision.SKIP


# ---------------------------------------------------------------------------
# Stage 2 — T+72h
# ---------------------------------------------------------------------------


def test_wake_at_t72h_with_one_prior_wake():
    item = _item(
        wake_count=1,
        first_open_at=(NOW - timedelta(days=5)).isoformat(),
        last_woken_at=(NOW - timedelta(hours=48)).isoformat(),
    )
    assert should_wake_action_item(item, now=NOW) == ActionItemWakeDecision.WAKE


def test_skip_between_t24_and_t72_with_one_prior_wake():
    item = _item(
        wake_count=1,
        first_open_at=(NOW - timedelta(hours=48)).isoformat(),
        last_woken_at=(NOW - timedelta(hours=24)).isoformat(),
    )
    assert should_wake_action_item(item, now=NOW) == ActionItemWakeDecision.SKIP


# ---------------------------------------------------------------------------
# Stage 3 — every 7d after the 72h wake
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("wake_count", [2, 3])
def test_wake_every_7d_after_72h(wake_count):
    item = _item(
        wake_count=wake_count,
        first_open_at=(NOW - timedelta(days=30)).isoformat(),
        last_woken_at=(NOW - timedelta(days=7)).isoformat(),
    )
    assert should_wake_action_item(item, now=NOW) == ActionItemWakeDecision.WAKE


def test_skip_between_7d_intervals():
    item = _item(
        wake_count=2,
        first_open_at=(NOW - timedelta(days=10)).isoformat(),
        last_woken_at=(NOW - timedelta(days=6)).isoformat(),
    )
    assert should_wake_action_item(item, now=NOW) == ActionItemWakeDecision.SKIP


# ---------------------------------------------------------------------------
# Stage 4 — stale after the 4th unanswered wake
# ---------------------------------------------------------------------------


def test_stale_seven_days_after_fourth_wake():
    item = _item(
        wake_count=4,
        first_open_at=(NOW - timedelta(days=30)).isoformat(),
        last_woken_at=(NOW - timedelta(days=7)).isoformat(),
    )
    assert should_wake_action_item(item, now=NOW) == ActionItemWakeDecision.STALE


def test_skip_when_fourth_wake_just_fired():
    """The 4th wake itself is a WAKE; only +7d after does it become STALE."""
    item = _item(
        wake_count=4,
        first_open_at=(NOW - timedelta(days=10)).isoformat(),
        last_woken_at=(NOW - timedelta(minutes=1)).isoformat(),
    )
    assert should_wake_action_item(item, now=NOW) == ActionItemWakeDecision.SKIP


# ---------------------------------------------------------------------------
# Eligibility guards — never wake closed / unassigned / already-stale
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["done", "cancelled"])
def test_skip_non_open_items(status):
    item = _item(status=status)
    assert should_wake_action_item(item, now=NOW) == ActionItemWakeDecision.SKIP


def test_skip_unassigned_items():
    item = _item(owner_agent_id=None)
    assert should_wake_action_item(item, now=NOW) == ActionItemWakeDecision.SKIP


def test_skip_already_stale_items():
    item = _item(
        wake_count=4,
        first_open_at=(NOW - timedelta(days=30)).isoformat(),
        last_woken_at=(NOW - timedelta(days=14)).isoformat(),
        stale_at=(NOW - timedelta(days=7)).isoformat(),
    )
    assert should_wake_action_item(item, now=NOW) == ActionItemWakeDecision.SKIP


def test_skip_when_first_open_at_missing():
    """Backfill should have stamped this — skip conservatively rather than
    waking an item whose escalation anchor is unknown (plan §3 invariant)."""
    item = _item(first_open_at=None)
    assert should_wake_action_item(item, now=NOW) == ActionItemWakeDecision.SKIP


def test_skip_garbage_payload():
    """Defensive: a non-dict payload must not raise."""
    assert should_wake_action_item(None, now=NOW) == ActionItemWakeDecision.SKIP  # type: ignore[arg-type]
    assert should_wake_action_item("not-a-dict", now=NOW) == ActionItemWakeDecision.SKIP


# ---------------------------------------------------------------------------
# scan_pending_action_items — persona-scoped
# ---------------------------------------------------------------------------


def test_scan_returns_only_owned_items():
    items = [
        _item(id="a1", owner_agent_id=OWNER),  # wake: SKIP (1h after open)
        _item(id="a2", owner_agent_id=OTHER),  # filtered out
        _item(
            id="a3",
            owner_agent_id=OWNER,
            wake_count=0,
            first_open_at=(NOW - timedelta(hours=25)).isoformat(),
        ),  # wake: WAKE
    ]
    out = scan_pending_action_items(items, persona_agent_id=OWNER, now=NOW)
    assert [item_id for item_id, _ in out] == ["a1", "a3"]
    assert [d for _, d in out] == [
        ActionItemWakeDecision.SKIP,
        ActionItemWakeDecision.WAKE,
    ]


def test_scan_with_none_persona_keeps_all_items():
    """No persona restriction → every dict item is considered.

    Used by dogfood / diagnostic scripts that want to inspect all items
    regardless of ownership.
    """
    items = [
        _item(id="a1", owner_agent_id=OWNER),
        _item(id="a2", owner_agent_id=OTHER),
    ]
    out = scan_pending_action_items(items, persona_agent_id=None, now=NOW)
    assert len(out) == 2


def test_scan_skips_entries_without_id():
    items = [
        _item(id="a1"),
        {"status": "open", "owner_agent_id": OWNER},  # no id → skipped
        "not-a-dict",
    ]
    out = scan_pending_action_items(items, persona_agent_id=OWNER, now=NOW)
    assert [item_id for item_id, _ in out] == ["a1"]


# ---------------------------------------------------------------------------
# Threshold constants — pin to plan §3 numbers
# ---------------------------------------------------------------------------


def test_thresholds_match_plan_section_three():
    """Locks the decision thresholds to plan §3 numbers. If the plan moves,
    update both ``cli/action_item_escalation.py`` and
    ``server/services/action_item_service.py`` together; the canonical service
    test already cross-checks them.
    """
    from cli.action_item_escalation import (
        _WAKE_MAX_BEFORE_STALE,
        _WAKE_REPEAT_DAYS,
        _WAKE_STAGE_HOURS,
    )

    assert _WAKE_STAGE_HOURS == ((1, 24), (2, 72))
    assert _WAKE_REPEAT_DAYS == 7
    assert _WAKE_MAX_BEFORE_STALE == 4
