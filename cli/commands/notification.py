"""``map notification ...`` + ``map inbound-event ...`` sub-apps — arch PR5.

Wraps the personal-inbox / waker-event surface:

* ``map notification list|read|read-all`` —
  ``GET /api/v1/notifications`` + ``POST /notifications/{id}/read`` +
  ``POST /notifications/read-all``. Mirrors the wakeable / digest view
  surfaced in the web todo UI.
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

notification_app = typer.Typer(help="Notification commands (personal inbox)")


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
    """List the current persona's notifications."""
    from cli.main import _run  # lazy: avoid cli.main ↔ cli.commands.* cycle

    def action(c: Any) -> Any:
        return c.list_notifications(
            unread_only=unread_only,
            category=category,
            target_type=target_type,
            limit=limit,
            offset=offset,
        )

    _run(action)


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
def notification_read_all() -> None:
    """Mark every notification for the current persona as read."""
    from cli.main import _run

    def action(c: Any) -> Any:
        return c.mark_all_notifications_read()

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

    from cli.main import _read_text_file, _run
    from server.domain.schemas import InboundEventCreate

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
