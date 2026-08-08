"""``map feedback ...`` sub-app — arch experiment 0519e2a3 PR7.

Platform feedback inbox. ``submit`` is persona-scoped; ``list`` / ``get`` /
``update`` are admin-only (used for triage).

All command bodies lazy-import ``cli.main._run`` to break the
``cli.main ↔ cli.commands.*`` import cycle.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import typer
from map_client.client import MAPClient

feedback_app = typer.Typer(help="Platform feedback inbox commands")


@feedback_app.command("submit")
def feedback_submit(
    body: str | None = typer.Option(None, "--body", help="Feedback text (free-form)."),
    body_file: Path | None = typer.Option(
        None,
        "--file",
        help="Read feedback body from a file (avoids shell-quoting issues with backticks / $vars).",
    ),
    category: str | None = typer.Option(
        None, "--category", help="bug|suggestion|question|other (optional, admin triage hint)"
    ),
    project: uuid.UUID | None = typer.Option(
        None, "--project", help="Source project context (optional)"
    ),
) -> None:
    from map_types.enums import FeedbackCategory
    from map_types.schemas import PlatformFeedbackCreate

    from cli.main import _read_text_file, _run  # lazy: avoid cli.main ↔ cli.commands.* cycle

    if body is None and body_file is None:
        typer.echo("Error: either --body or --file is required", err=True)
        raise typer.Exit(2)
    if body is not None and body_file is not None:
        typer.echo("Error: use only one of --body or --file", err=True)
        raise typer.Exit(2)
    content = body if body is not None else _read_text_file(body_file, kind="feedback")
    payload = PlatformFeedbackCreate(
        body=content,
        project_id=project,
        category=FeedbackCategory(category) if category else None,
    )
    _run(lambda c: c.submit_feedback(payload))


@feedback_app.command("list")
def feedback_list(
    status: str | None = typer.Option(None, "--status"),
    category: str | None = typer.Option(None, "--category"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    page: int = typer.Option(1, "--page", min=1),
    page_size: int = typer.Option(50, "--page-size", min=1, max=200),
    include_archived: bool = typer.Option(False, "--include-archived"),
) -> None:
    from map_types.enums import FeedbackCategory, FeedbackStatus

    from cli.main import _run  # lazy

    def action(c: MAPClient):
        items, total = c.list_feedback_page(
            status=FeedbackStatus(status) if status else None,
            category=FeedbackCategory(category) if category else None,
            project_id=project,
            page=page,
            page_size=page_size,
            include_archived=include_archived,
        )
        return {"items": items, "total": total}

    _run(action, admin=True)


@feedback_app.command("get")
def feedback_get(feedback_id: uuid.UUID = typer.Argument(..., help="Feedback UUID")) -> None:
    from cli.main import _run  # lazy

    _run(lambda c: c.get_feedback(feedback_id), admin=True)


@feedback_app.command("update")
def feedback_update(
    feedback_id: uuid.UUID = typer.Argument(..., help="Feedback UUID"),
    status: str | None = typer.Option(None, "--status"),
    category: str | None = typer.Option(None, "--category"),
    archived: bool | None = typer.Option(None, "--archived/--no-archived"),
) -> None:
    from map_types.enums import FeedbackCategory, FeedbackStatus
    from map_types.schemas import PlatformFeedbackUpdate

    from cli.main import _run  # lazy

    payload = PlatformFeedbackUpdate(
        status=FeedbackStatus(status) if status else None,
        category=FeedbackCategory(category) if category else None,
        archived=archived,
    )
    _run(lambda c: c.update_feedback(feedback_id, payload), admin=True)
