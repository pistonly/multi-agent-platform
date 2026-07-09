"""Tests for race experiment (eca0f522) PR3 simple-waker per-cycle dedup.

The PR3 invariant is: when multiple wakeable notifications share the same
``group_key`` within one polling cycle, the waker must:

1. keep only the highest ``wake_version`` row (most recent merge)
2. ``notification_count`` reflect the deduped unique count
3. **preserve the raw ``event_count``** so audit visibility into the
   underlying event volume is not compressed by dedup
4. surface ``notification_dedup_dropped`` for observability

We do NOT depend on the DB-layer UNIQUE constraint from PR2 — the waker
must be defensive regardless of whether dupes reach it.
"""

from __future__ import annotations

from typing import Any

import pytest

from cli.simple_waker import build_wake_context


def _notif(
    group_key: str | None,
    wake_version: int,
    event_count: int = 1,
    *,
    nid: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": nid or f"n-{group_key}-{wake_version}",
        "group_key": group_key,
        "wake_version": wake_version,
        "event_count": event_count,
    }
    return out


def test_pr3_unique_group_keys_pass_through() -> None:
    """Three notifications with three distinct group_keys → all kept."""
    notifs = [
        _notif("a", 1, event_count=2),
        _notif("b", 1, event_count=1),
        _notif("c", 3, event_count=5),
    ]
    ctx = build_wake_context(
        topic_progress_data=None,
        todos={},
        notifications=notifs,
    )
    assert ctx.notification_count == 3
    assert ctx.notification_event_count_sum == 8
    assert ctx.notification_dedup_dropped == 0


def test_pr3_duplicate_group_key_keeps_highest_wake_version() -> None:
    """Same group_key with multiple wake_versions → only the highest wins."""
    notifs = [
        _notif("lock:e1", 1, event_count=1),
        _notif("lock:e1", 2, event_count=1),
        _notif("lock:e1", 3, event_count=1),
    ]
    ctx = build_wake_context(
        topic_progress_data=None,
        todos={},
        notifications=notifs,
    )
    assert ctx.notification_count == 1
    assert ctx.notification_dedup_dropped == 2
    # Only the surviving row's event_count is summed.
    assert ctx.notification_event_count_sum == 1


def test_pr3_event_count_sum_preserves_raw_volume() -> None:
    """Race PR3 acceptance: audit must preserve raw event_count.

    Two wakeable rows for ``exp-1`` with event_count=4 and event_count=7
    — the latter has higher wake_version, so it survives; but the raw
    total (11) must still be visible to the audit path.
    """
    notifs = [
        _notif("exp-1", 1, event_count=4),
        _notif("exp-1", 2, event_count=7),
    ]
    ctx = build_wake_context(
        topic_progress_data=None,
        todos={},
        notifications=notifs,
    )
    assert ctx.notification_count == 1
    # event_count on surviving row is 7 — that's the "deduped" view.
    assert ctx.notification_event_count_sum == 7
    # And we report how many rows got dropped for observability.
    assert ctx.notification_dedup_dropped == 1


def test_pr3_null_group_key_treated_as_unique() -> None:
    """group_key=NULL rows are never deduped (UNIQUE constraints ignore
    NULLs, so the DB may legitimately carry multiple such rows).
    """
    notifs = [
        _notif(None, 1, event_count=1),
        _notif(None, 1, event_count=1),
    ]
    ctx = build_wake_context(
        topic_progress_data=None,
        todos={},
        notifications=notifs,
    )
    assert ctx.notification_count == 2
    assert ctx.notification_event_count_sum == 2
    assert ctx.notification_dedup_dropped == 0


def test_pr3_empty_notifications_no_op() -> None:
    ctx = build_wake_context(
        topic_progress_data=None,
        todos={},
        notifications=None,
    )
    assert ctx.notification_count == 0
    assert ctx.notification_event_count_sum == 0
    assert ctx.notification_dedup_dropped == 0


def test_pr3_dedup_does_not_affect_todo_count() -> None:
    """Dedup is scoped to notifications; todos stay raw."""
    todos = {
        "pending_topic_replies": [{"comment_id": "c1"}],
    }
    notifs = [
        _notif("exp-1", 1, event_count=1),
        _notif("exp-1", 2, event_count=1),
    ]
    ctx = build_wake_context(
        topic_progress_data=None,
        todos=todos,
        notifications=notifs,
    )
    assert ctx.notification_count == 1
    assert ctx.todo_item_count == 1
    # total_items still uses deduped notification_count.
    assert ctx.total_items == 2


def test_pr3_dedup_with_non_dict_entries_tolerated() -> None:
    """The dedup helper must not crash on malformed notification rows."""
    notifs = [
        _notif("a", 1, event_count=2),
        "not a dict",  # type: ignore[list-item]
        None,  # type: ignore[list-item]
        _notif("a", 2, event_count=3),
    ]
    ctx = build_wake_context(
        topic_progress_data=None,
        todos={},
        notifications=notifs,
    )
    assert ctx.notification_count == 1
    assert ctx.notification_event_count_sum == 3
