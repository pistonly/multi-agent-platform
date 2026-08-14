"""cli-ux PR2: notification read-all --category / --event bulk filter.

Guards two contracts:

1. ``map notification read-all --category wakeable`` only marks notifications
   whose category is ``wakeable`` (not digest).
2. ``map notification read-all --event <type>`` only marks notifications
   whose event equals ``<type>``.
3. ``map notification read-all`` (no filter) still uses the single bulk
   endpoint, NOT enumerate-then-mark.

Tests use ``CliRunner`` against the live typer app; no subprocess needed.
"""
from __future__ import annotations

from datetime import timezone
from unittest.mock import MagicMock

import pytest
from map_types.enums import NotificationCategory
from map_types.schemas import NotificationListRead, NotificationRead
from typer.testing import CliRunner

from cli.commands.notification import notification_app


def _mk_notification(
    *, nid: str, category: str, event: str, unread: bool = True
) -> NotificationRead:
    """Build a NotificationRead with the fields the CLI reads."""
    import uuid
    from datetime import datetime

    return NotificationRead(
        id=uuid.UUID(nid),
        recipient_agent_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        event=event,
        summary=f"{event} for {nid[:8]}",
        target_type="experiment",
        target_id=uuid.uuid4(),
        payload_json={"id": nid, "phase": "done"},
        category=NotificationCategory(category),
        group_key=f"test:{nid}",
        wake_version=1,
        fingerprint_version="v2",
        event_count=1,
        first_event_at=datetime.now(timezone.utc),
        last_event_at=datetime.now(timezone.utc),
        read_at=None if unread else datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def fake_client() -> MagicMock:
    """A fake MAPClient that records mark_notification_read calls."""
    client = MagicMock()
    client.mark_notification_read.return_value = {"id": "x", "read_at": "now"}
    return client


def _patch_run(monkeypatch: pytest.MonkeyPatch, client: MagicMock) -> None:
    """Patch ``cli.main._run`` so it invokes ``action(client)`` synchronously."""
    import cli.main as cli_main

    def _fake_run(action, **kwargs):  # type: ignore[no-untyped-def]
        return action(client)

    monkeypatch.setattr(cli_main, "_run", _fake_run)


def test_read_all_no_filter_uses_bulk_endpoint(
    runner: CliRunner, fake_client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No filter → single bulk call, NOT enumeration."""
    fake_client.mark_all_notifications_read.return_value = {"marked": 42}
    _patch_run(monkeypatch, fake_client)

    result = runner.invoke(notification_app, ["read-all"])
    assert result.exit_code == 0, result.output
    fake_client.mark_all_notifications_read.assert_called_once()
    # No enumeration path was taken.
    fake_client.list_notifications.assert_not_called()
    fake_client.mark_notification_read.assert_not_called()


def test_read_all_with_category_uses_enumerate_path(
    runner: CliRunner, fake_client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--category wakeable → enumerate list with category filter, mark each."""
    # Page contains 1 wakeable + 1 digest, with category filter the API
    # already restricts to wakeable; the CLI marks both returned items.
    fake_client.list_notifications.return_value = NotificationListRead(
        items=[
            _mk_notification(
                nid="11111111-1111-1111-1111-111111111111",
                category="wakeable",
                event="experiment.phase_changed",
            ),
            _mk_notification(
                nid="22222222-2222-2222-2222-222222222222",
                category="wakeable",
                event="experiment.phase_changed",
            ),
        ],
        total=2,
        unread_count=2,
    )
    _patch_run(monkeypatch, fake_client)

    result = runner.invoke(notification_app, ["read-all", "--category", "wakeable"])
    assert result.exit_code == 0, result.output
    # Bulk endpoint NOT used.
    fake_client.mark_all_notifications_read.assert_not_called()
    # list was called with category filter.
    list_kwargs = fake_client.list_notifications.call_args.kwargs
    assert list_kwargs["category"] == NotificationCategory.wakeable
    assert list_kwargs["unread_only"] is True
    # Both items marked.
    assert fake_client.mark_notification_read.call_count == 2


def test_read_all_with_event_filter_filters_client_side(
    runner: CliRunner, fake_client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--event filters client-side after list (API has no event filter)."""
    fake_client.list_notifications.return_value = NotificationListRead(
        items=[
            _mk_notification(
                nid="33333333-3333-3333-3333-333333333333",
                category="digest",
                event="experiment.phase_changed",
            ),
            _mk_notification(
                nid="44444444-4444-4444-4444-444444444444",
                category="digest",
                event="review.submitted",  # different event
            ),
        ],
        total=2,
        unread_count=2,
    )
    _patch_run(monkeypatch, fake_client)

    result = runner.invoke(
        notification_app,
        ["read-all", "--event", "experiment.phase_changed"],
    )
    assert result.exit_code == 0, result.output
    # Only the experiment.phase_changed item is marked.
    marked_ids = [
        call.args[0] for call in fake_client.mark_notification_read.call_args_list
    ]
    assert len(marked_ids) == 1
    import uuid as _uuid
    assert marked_ids[0] == _uuid.UUID("33333333-3333-3333-3333-333333333333")


def test_read_all_with_invalid_category_exits_clean(
    runner: CliRunner, fake_client: MagicMock
) -> None:
    """--category foo → exit 2 + clean error message (no API call)."""
    result = runner.invoke(notification_app, ["read-all", "--category", "foo"])
    assert result.exit_code == 2
    assert "wakeable" in result.output or "wakeable" in (result.stderr or "")
    fake_client.list_notifications.assert_not_called()


def test_read_all_with_empty_page_returns_zero_marked(
    runner: CliRunner, fake_client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--category wakeable with no matching notifications → exit 0, marked=0."""
    fake_client.list_notifications.return_value = NotificationListRead(
        items=[], total=0, unread_count=0
    )
    _patch_run(monkeypatch, fake_client)

    result = runner.invoke(notification_app, ["read-all", "--category", "digest"])
    assert result.exit_code == 0, result.output
    fake_client.mark_notification_read.assert_not_called()


def test_help_text_mentions_read_all(
    runner: CliRunner, fake_client: MagicMock
) -> None:
    """``map notification --help`` lists ``read-all`` with new description."""
    result = runner.invoke(notification_app, ["--help"])
    assert result.exit_code == 0
    assert "read-all" in result.output
    # Help mentions the new "(filtered)" wording so users know bulk has filters.
    assert "filtered" in result.output
