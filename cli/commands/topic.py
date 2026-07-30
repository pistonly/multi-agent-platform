"""``map topic ...`` + ``map mention ...`` + ``map todo ...`` sub-apps — arch PR6.

Three sub-apps that share the "topic work items" domain:

* ``map topic ...`` — topic lifecycle (create / list / show / progress /
  resolve / advance-round / comment / close / reopen / dismiss / read /
  mark-seen / archive). The big one.
* ``map mention ...`` — personal @mention todos (dismiss / list /
  dismiss-all / reconcile-stale stub).
* ``map todo ...`` — explicit_only todo partition clear router
  (notification / action_item / my_open_topics / unread_change).

All command bodies lazy-import ``cli.main._run`` and friends to break
the ``cli.main ↔ cli.commands.*`` import cycle.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import typer
from map_client.client import MAPClient

from cli.table_render import enum_value, format_datetime, render_table, short_uuid, truncate
from server.domain.schemas import (
    TopicAdvanceRound,
    TopicCommentCreate,
    TopicUpdate,
)

topic_app = typer.Typer(help="Topic commands", rich_markup_mode=None)
mention_app = typer.Typer(help="Mention todo commands")
todo_app = typer.Typer(help="Todo partition clear routing (explicit_only buckets)")


# ---------------------------------------------------------------------------
# topic_app
# ---------------------------------------------------------------------------


@topic_app.command("create")
def topic_create(
    title: str = typer.Option(..., "--title"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    description: str | None = typer.Option(None, "--description"),
) -> None:
    from cli.main import _resolve_project, _run
    from server.domain.schemas import TopicCreate

    payload = TopicCreate(title=title, description=description)

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        return c.create_topic(pid, payload)

    _run(action)


def _render_topic_table(topics: Any) -> str:
    """Render a list of TopicSummaryRead as a compact table."""
    headers = ["ID", "Title", "Status", "Round", "Comments", "Exps", "Creator", "Created"]
    rows = []
    for t in topics:
        rows.append([
            short_uuid(t.id),
            truncate(t.title, 50),
            enum_value(t.status),
            enum_value(t.discussion_round),
            str(t.comment_count),
            str(t.experiment_count),
            truncate(t.creator_name, 20),
            format_datetime(t.created_at),
        ])
    return render_table(headers, rows)


@topic_app.command("list")
def topic_list(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    status: str | None = typer.Option(None, "--status"),
    creator: str | None = typer.Option(
        None,
        "--creator",
        help="Filter by topic creator. Accepts agent_name (current project) or agent_id UUID; "
        "alias for --creator-agent-id.",
    ),
    creator_agent_id: uuid.UUID | None = typer.Option(
        None,
        "--creator-agent-id",
        help="Filter by creator agent_id UUID. Use --creator for name-or-id shorthand.",
    ),
    q: str | None = typer.Option(None, "--q"),
    page: int = typer.Option(1, "--page", min=1),
    page_size: int = typer.Option(100, "--page-size", min=1, max=100),
    include_archived: bool = typer.Option(False, "--include-archived"),
) -> None:
    """List topics in the current project.

    Defaults to a compact table view. Use ``--format yaml`` or
    ``--format json`` for full structured output (scripts / piping).
    """
    from cli.main import _resolve_creator_agent_id, _resolve_project, _run
    from server.domain.models import TopicStatus

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        st = TopicStatus(status) if status else None
        resolved_creator_id = _resolve_creator_agent_id(c, pid, creator, creator_agent_id)
        return c.list_topics(
            pid,
            status=st,
            creator_agent_id=resolved_creator_id,
            q=q,
            page=page,
            page_size=page_size,
            include_archived=include_archived,
        )

    _run(action, table_renderer=_render_topic_table)


@topic_app.command("show")
def topic_show(topic_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    from cli.main import _run

    _run(lambda c: c.get_topic(topic_id))


@topic_app.command("progress")
def topic_progress() -> None:
    """Per-agent topic work items view (obligation + contextual); same source as todos topic buckets."""
    from cli.main import _run

    _run(lambda c: c.get_topic_progress())


@topic_app.command("resolve")
def topic_resolve(
    topic_id: uuid.UUID = typer.Option(..., "--id"),
    resolve_file: Path = typer.Option(..., "--file"),
) -> None:
    from cli.main import _load_topic_resolve_payload, _run

    payload = _load_topic_resolve_payload(resolve_file)
    _run(lambda c: c.resolve_topic(topic_id, payload))


@topic_app.command("advance-round")
def topic_advance_round(
    topic_id: uuid.UUID = typer.Option(..., "--id"),
    increment_summary: bool = typer.Option(
        True,
        "--increment-summary/--no-increment-summary",
        help="Increment round_summary_count before advancing.",
    ),
    ack_ids: str | None = typer.Option(
        None,
        "--ack-ids",
        help="Host: comma-separated participant agent UUIDs already acknowledged.",
    ),
    ack: str | None = typer.Option(
        None,
        "--ack",
        help="Participant: accept, reject, or dismiss acknowledgement for the current round.",
    ),
    mark_ready: bool = typer.Option(
        False,
        "--ready",
        help="Mark topic as ready for experiment creation instead of advancing to the next round.",
    ),
    waive_ack: bool = typer.Option(
        False,
        "--waive-ack",
        help="Host: waive the participant ack requirement and advance immediately (requires --waive-reason).",
    ),
    waive_reason: str | None = typer.Option(
        None,
        "--waive-reason",
        help="Reason for waiving the ack requirement (required when --waive-ack is set).",
    ),
) -> None:
    from cli.main import _run

    acknowledged_by: list[uuid.UUID] = []
    if ack_ids:
        acknowledged_by = [uuid.UUID(item.strip()) for item in ack_ids.split(",") if item.strip()]
    payload = TopicAdvanceRound(
        increment_summary=increment_summary,
        acknowledged_by=acknowledged_by,
        ack=ack,  # type: ignore[arg-type]
        mark_ready=mark_ready,
        waive_ack=waive_ack,
        waive_reason=waive_reason,
    )
    _run(lambda c: c.advance_topic_round(topic_id, payload))


@topic_app.command("rollback-round")
def topic_rollback_round(
    topic_id: uuid.UUID = typer.Option(..., "--id"),
) -> None:
    """Roll the discussion round back by one step (roundN → roundN-1, or ready → roundN)."""
    from cli.main import _run

    _run(lambda c: c.rollback_topic_round(topic_id))


@topic_app.command("comment")
def topic_comment(
    topic_id: uuid.UUID = typer.Option(..., "--id"),
    body: str | None = typer.Option(None, "--body"),
    body_file: Path | None = typer.Option(None, "--file"),
    parent: uuid.UUID | None = typer.Option(None, "--parent"),
    round_summary: bool = typer.Option(
        False,
        "--round-summary",
        help="Mark this comment as a Round Summary (triggers participant ack flow).",
    ),
) -> None:
    from cli.main import _read_text_file, _run

    if body is None and body_file is None:
        typer.echo("Error: either --body or --file is required", err=True)
        raise typer.Exit(2)
    if body is not None and body_file is not None:
        typer.echo("Error: use only one of --body or --file", err=True)
        raise typer.Exit(2)
    content = body if body is not None else _read_text_file(body_file, kind="comment")
    payload = TopicCommentCreate(body=content, parent_id=parent, is_round_summary=round_summary)
    _run(lambda c: c.create_topic_comment(topic_id, payload))


@topic_app.command("close")
def topic_close(
    topic_id: uuid.UUID = typer.Option(..., "--id"),
    reason: str | None = typer.Option(
        None,
        "--reason",
        help="Short reason code for closing (e.g. 'no_experiment_needed', 'superseded').",
    ),
    note: str | None = typer.Option(
        None,
        "--note",
        help="Longer explanation for why the topic is being closed.",
    ),
) -> None:
    from cli.main import _run

    _run(lambda c: c.close_topic(topic_id, close_reason=reason, close_note=note))


@topic_app.command("reopen")
def topic_reopen(topic_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    from cli.main import _run

    _run(lambda c: c.reopen_topic(topic_id))


@topic_app.command("dismiss")
def topic_dismiss(topic_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    """Hide an open topic from host todos until new activity (same as Web UI ✕)."""
    from cli.main import _run

    _run(lambda c: c.dismiss_topic(topic_id))


@topic_app.command("read")
def topic_read(topic_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    """Mark contextual unread changes as seen; obligations still require reply/ack/mention handling."""
    from cli.main import _run

    _run(lambda c: c.mark_topic_read(topic_id))


@topic_app.command("mark-seen")
def topic_mark_seen(topic_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    """Alias of topic read: clears contextual unread only, not reply/ack/mention obligations."""
    from cli.main import _run

    _run(lambda c: c.mark_topic_read(topic_id))


@topic_app.command(
    "archive",
    epilog="Use --undo or --unarchive to restore an archived topic.",
)
def topic_archive(
    topic_id: uuid.UUID | None = typer.Option(None, "--id", help="Topic UUID."),
    undo: bool = typer.Option(
        False,
        "--undo",
        help="Unarchive instead of archive. Equivalent to --unarchive.",
    ),
    unarchive: bool = typer.Option(
        False,
        "--unarchive",
        help="Alias of --undo: unarchive instead of archive.",
    ),
) -> None:
    """Archive (or unarchive) a topic.

    Thin wrapper around ``PATCH /topics/{id}`` with ``archived=true`` (or
    ``false`` when ``--undo``/``--unarchive`` is set). Archive hides the
    topic from ``topic list`` by default but ``topic show`` still returns
    it including ``archived_at``. Archive is reversible — re-run with
    ``--undo`` to restore.
    """
    from map_client.exceptions import MAPNotFoundError

    from cli.main import _require_option_uuid, _run

    topic_id = _require_option_uuid(topic_id)
    payload = TopicUpdate(archived=not (undo or unarchive))
    object_kind = "topic"

    def action(c: MAPClient):
        try:
            return c.update_topic(topic_id, payload)
        except MAPNotFoundError as exc:
            typer.echo(
                f"Error: {object_kind} {topic_id} not found",
                err=True,
            )
            raise typer.Exit(1) from exc

    _run(action)


# ---------------------------------------------------------------------------
# mention_app
# ---------------------------------------------------------------------------


@mention_app.command("dismiss")
def mention_dismiss(
    mention_id: uuid.UUID = typer.Option(..., "--id", help="Mention UUID from `map todos`."),
) -> None:
    """Dismiss one @mention for the current persona (removes it from `map todos`).

    Idempotent: dismissing an already-dismissed mention returns the same result.
    """
    from cli.main import _run

    _run(lambda c: c.dismiss_mention(mention_id))


@mention_app.command("list")
def mention_list() -> None:
    """List open @mentions for the current persona."""
    from cli.main import _run

    _run(lambda c: c.get_todos().mentions)


@mention_app.command("dismiss-all")
def mention_dismiss_all() -> None:
    """Dismiss all open @mentions for the current persona."""
    from cli.main import _run

    _run(lambda c: c.dismiss_all_mentions())


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


@todo_app.command("clear")
def todo_clear(
    key: str = typer.Option(..., "--key", help="Work-item idempotency_key or partition id"),
) -> None:
    """Route explicit_only todo partitions to the canonical clear CLI (T1 D7)."""
    from cli.main import _run

    if key.startswith("notification:"):
        notification_id = uuid.UUID(key.split(":", 1)[1])
        _run(lambda c: c.mark_notification_read(notification_id))
        return
    if key.startswith("action_item:"):
        item_id = uuid.UUID(key.split(":", 1)[1])
        _run(lambda c: c.complete_action_item(item_id))
        return
    if key.startswith("my_open_topics:") or key.startswith("topic:"):
        topic_id = uuid.UUID(key.rsplit(":", 1)[-1])
        _run(lambda c: c.dismiss_topic(topic_id))
        return
    if key.startswith("unread_change:"):
        topic_id = uuid.UUID(key.split(":", 2)[1])
        _run(lambda c: c.mark_topic_read(topic_id))
        return
    raise typer.BadParameter(
        f"unsupported todo clear key {key!r}; explicit_only: notification, action_item, "
        "my_open_topics, unread_change"
    )
