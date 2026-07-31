"""``map notification ...`` + ``map inbound-event ...`` sub-apps — arch PR5.

Wraps the personal-inbox / waker-event surface:

* ``map notification list`` —
  ``GET /api/v1/notifications`` with category/target_type/unread_only filters.
* ``map notification read`` —
  ``POST /notifications/{id}/read`` (one notification at a time).
* ``map notification read-all`` (cli-ux PR2: + ``--category`` + ``--event``) —
  ``POST /notifications/read-all`` for the unfiltered fast path, OR enumerate
  via ``list`` + ``mark_notification_read`` for filtered bulk. Mirrors the
  wakeable / digest view surfaced in the web todo UI.
* ``map inbound-event record`` —
  ``POST /api/v1/agents/me/inbound-events``. Waker-side D6 gate;
  on 409 the CLI exits non-zero so the waker treats it as
  "already woken" and skips resume.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import typer
from map_client.client import MAPClient
from map_client.exceptions import MAPConflictError

from cli.table_render import enum_value, format_datetime, render_table, short_uuid, truncate

notification_app = typer.Typer(help="Notification commands (personal inbox)")


def _render_notification_table(result: Any) -> str:
    """Render a NotificationListRead as a compact table with summary footer."""
    items = result.items if hasattr(result, "items") else result
    total = getattr(result, "total", len(items))
    unread = getattr(result, "unread_count", 0)

    headers = ["ID", "Event", "Summary", "Category", "Read", "Created"]
    rows = []
    for n in items:
        rows.append([
            short_uuid(n.id),
            truncate(n.event, 30),
            truncate(n.summary, 50),
            enum_value(n.category),
            "yes" if n.read_at else "no",
            format_datetime(n.created_at),
        ])
    table = render_table(headers, rows)
    footer = f"\n({len(items)} shown, {unread} unread / {total} total)"
    return table + footer


@notification_app.command("list")
def notification_list(
    unread_only: bool = typer.Option(False, "--unread-only", help="Only show unread."),
    category: str | None = typer.Option(
        None, "--category", help="wakeable | digest | all (filter by category)."
    ),
    target_type: str | None = typer.Option(
        None, "--target-type", help="Filter by notification.target_type."
    ),
    limit: int = typer.Option(50, "--limit", min=1, max=200),
    offset: int = typer.Option(0, "--offset", min=0),
) -> None:
    """List the current persona's notifications.

    Defaults to a compact table view. Use ``--format yaml`` or
    ``--format json`` for full structured output (scripts / piping).
    """
    from cli.main import _run  # lazy: avoid cli.main ↔ cli.commands.* cycle

    def action(c: Any) -> Any:
        return c.list_notifications(
            unread_only=unread_only,
            category=category,
            target_type=target_type,
            limit=limit,
            offset=offset,
        )

    _run(action, table_renderer=_render_notification_table)


@notification_app.command("read")
def notification_read(
    notification_id: uuid.UUID = typer.Option(..., "--id", help="Notification UUID."),
) -> None:
    """Mark one notification as read."""
    from cli.main import _run

    def action(c: Any) -> Any:
        return c.mark_notification_read(notification_id)

    _run(action)


@notification_app.command("read-all")
def notification_read_all(
    category: str | None = typer.Option(
        None,
        "--category",
        help="Filter by category before bulk-marking: wakeable | digest | all (default = all).",
    ),
    event: str | None = typer.Option(
        None,
        "--event",
        help="Filter by notification.event before bulk-marking "
        "(client-side filter; e.g. mention.created, experiment.phase_changed).",
    ),
) -> None:
    """Mark every (filtered) notification for the current persona as read.

    Without filters this hits the bulk ``POST /agents/me/notifications/read-all``
    endpoint in a single round-trip. With ``--category`` or ``--event`` set, the
    CLI enumerates the matching subset via ``list_notifications`` then marks each
    via ``mark_notification_read`` (still fewer round-trips than ``read --id``
    one-at-a-time, and doesn't require knowing IDs upfront).
    """
    from map_types.enums import NotificationCategory

    from cli.main import _run

    # Validate --category early so user sees a clean error before any API call.
    if category is not None and category not in ("wakeable", "digest", "all"):
        typer.echo(
            f"Error: --category must be one of wakeable|digest|all (got {category!r})",
            err=True,
        )
        raise typer.Exit(2)

    def action(c: Any) -> Any:
        if category is None and event is None:
            # Fast path: single bulk endpoint, no enumeration.
            return c.mark_all_notifications_read()

        # Filtered path: enumerate, then mark each match.
        list_kwargs: dict = {"unread_only": True, "limit": 200}
        if category is not None and category != "all":
            list_kwargs["category"] = NotificationCategory(category)
        # Pagination loop — most personas have < 200 unread, but loop if more.
        marked = 0
        offset = 0
        while True:
            list_kwargs["offset"] = offset
            page = c.list_notifications(**list_kwargs)
            if not page.items:
                break
            for n in page.items:
                if event is not None and getattr(n, "event", None) != event:
                    continue
                c.mark_notification_read(n.id)
                marked += 1
            offset += len(page.items)
            if offset >= page.total:
                break
        return {"marked": marked}

    _run(action)


inbound_event_app = typer.Typer(
    help="Runtime-waker inbound event commands (D6 server gate)"
)


@inbound_event_app.command("record")
def inbound_event_record(
    event_id: uuid.UUID = typer.Option(
        ..., "--event-id", help="Upstream notification id (UUID)."
    ),
    fingerprint: str = typer.Option(
        ..., "--fingerprint", help="Dedup key (server enforces UNIQUE per agent)."
    ),
    event_type: str = typer.Option(
        ...,
        "--event-type",
        help="Logical event type (e.g. mention, pending_review, topic_lifecycle).",
    ),
    source: str = typer.Option(
        "polling", "--source", help="polling | sse | replay (Phase 1 = polling)."
    ),
    payload_file: str | None = typer.Option(
        None, "--payload-file", help="Optional JSON file with extra payload fields."
    ),
) -> None:
    """Record that the caller is about to act on ``event_id``."""
    from map_types.enums import InboundEventSource
    from map_types.schemas import InboundEventCreate

    from cli.main import _read_text_file, _run

    extra_payload: dict | None = None
    if payload_file is not None:
        extra_payload = json.loads(_read_text_file(Path(payload_file), kind="payload"))
    payload = InboundEventCreate(
        event_id=event_id,
        event_type=event_type,
        source=InboundEventSource(source),
        fingerprint=fingerprint,
        payload=extra_payload,
    )

    def action(c: MAPClient):
        try:
            return c.record_inbound_event(payload)
        except MAPConflictError as exc:
            typer.echo(f"inbound-event duplicate (409): {exc.detail}", err=True)
            raise typer.Exit(2) from exc

    _run(action)
