"""``map mention ...`` sub-app — T45 拆分自 topic.py。"""
from __future__ import annotations

import uuid

import typer

from cli import runner  # module ref: test monkeypatch surface (T23)

mention_app = typer.Typer(help="Mention todo commands")


@mention_app.command("dismiss")
def mention_dismiss(
    mention_id: uuid.UUID = typer.Option(..., "--id", help="Mention UUID from `map todos`."),
) -> None:
    """Dismiss one @mention for the current persona (removes it from `map todos`).

    Idempotent: dismissing an already-dismissed mention returns the same result.
    """

    runner._run(lambda c: c.dismiss_mention(mention_id))


@mention_app.command("list")
def mention_list() -> None:
    """List open @mentions for the current persona."""

    runner._run(lambda c: c.get_todos().mentions)


@mention_app.command("dismiss-all")
def mention_dismiss_all() -> None:
    """Dismiss all open @mentions for the current persona."""

    runner._run(lambda c: c.dismiss_all_mentions())


@mention_app.command("reconcile-stale")
def mention_reconcile_stale() -> None:
    """Admin stub: offline stale mention reconciliation (T1 D5 MVP — not implemented)."""
    typer.echo(
        "mention reconcile-stale: stub only — stale mentions are filtered in "
        "topic-progress/todos projection; use write-path dismiss on comment."
    )


# ---------------------------------------------------------------------------
# todo_app — explicit_only partition clear router
# ---------------------------------------------------------------------------
