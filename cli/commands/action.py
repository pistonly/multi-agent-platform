"""``map action ...`` sub-app — arch experiment 0519e2a3 PR7.

Topic action item lifecycle. Action items are surfaced in
``map todos.action_items`` (waker D6 stage: WAKE / STALE escalation).
All command bodies lazy-import ``cli.main._run`` to break the
``cli.main ↔ cli.commands.*`` import cycle.
"""
from __future__ import annotations

import uuid

import typer
from map_client.client import MAPClient

from cli import runner  # module ref: test monkeypatch surface (T23)

action_app = typer.Typer(help="Topic action item commands")


@action_app.command("list")
def action_list(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    owner_agent_id: uuid.UUID | None = typer.Option(None, "--owner-agent-id"),
    mine: bool = typer.Option(False, "--mine", help="Only action items assigned to the current agent."),
    status: str | None = typer.Option("open", "--status"),
    limit: int = typer.Option(100, "--limit", min=1, max=200),
) -> None:
    from map_types.enums import TopicActionItemStatus


    def action(c: MAPClient):
        pid = runner._resolve_project(c, project, project_key)
        owner = owner_agent_id
        if mine:
            owner = c.get_me().id
        status_filter = TopicActionItemStatus(status) if status else None
        return c.list_project_action_items(
            pid,
            owner_agent_id=owner,
            status=status_filter,
            limit=limit,
        )

    runner._run(action)


@action_app.command("complete")
def action_complete(
    action_item_id: uuid.UUID = typer.Option(..., "--id", help="Action item UUID to mark done."),
) -> None:
    """Close an action item as done (open -> done)."""

    def action(c: MAPClient):
        return c.complete_action_item(action_item_id)

    runner._run(action)


@action_app.command("deliver")
def action_deliver(
    action_item_id: uuid.UUID = typer.Option(..., "--id", help="Action item UUID to deliver."),
) -> None:
    """Deliver an open action item (source topic may be closed/archived)."""

    def action(c: MAPClient):
        return c.deliver_action_item(action_item_id)

    runner._run(action)


@action_app.command("cancel")
def action_cancel(
    action_item_id: uuid.UUID = typer.Option(..., "--id", help="Action item UUID to cancel."),
    reason: str = typer.Option(..., "--reason", help="Cancellation reason (length-validated by category)."),
    category: str | None = typer.Option(
        None,
        "--category",
        help="implementation | decision | unspecified (default). Affects reason length threshold.",
    ),
) -> None:
    """Close an action item as cancelled (open -> cancelled)."""
    from map_types.enums import ActionItemCategory
    from map_types.schemas import ActionItemCancel


    def action(c: MAPClient):
        cat = ActionItemCategory(category) if category else None
        payload = ActionItemCancel(reason=reason, category=cat)
        return c.cancel_action_item(action_item_id, payload)

    runner._run(action)


@action_app.command("link")
def action_link(
    action_item_id: uuid.UUID = typer.Option(..., "--id", help="Action item UUID to link."),
    experiment_id: uuid.UUID = typer.Option(
        ...,
        "--experiment-id",
        help="Experiment UUID to attach to the action item (must share project).",
    ),
) -> None:
    """Attach an experiment to an open action item so future experiment
    ``done`` cascades the action item automatically."""

    def action(c: MAPClient):
        return c.link_action_item(action_item_id, experiment_id)

    runner._run(action)


@action_app.command("mark-wake-sent")
def action_mark_wake_sent(
    action_item_id: uuid.UUID = typer.Option(..., "--id", help="Action item UUID to wake."),
) -> None:
    """Bump wake_count + stamp last_woken_at + write ``action_item.wake_sent``
    audit row. Used by the runtime-waker CLI to advance the three-stage
    escalation timeline (experiment B / I4). Owner or admin only."""

    def action(c: MAPClient):
        return c.mark_wake_sent(action_item_id)

    runner._run(action)


@action_app.command("mark-stale")
def action_mark_stale(
    action_item_id: uuid.UUID = typer.Option(..., "--id", help="Action item UUID to mark stale."),
) -> None:
    """Stamp stale_at + write the ``action_item.stale`` audit row after the
    4th unanswered wake. Admin only (system escalation, experiment B / I4)."""

    def action(c: MAPClient):
        return c.mark_stale(action_item_id)

    runner._run(action)
