"""``map agent ...`` sub-app — arch experiment 0519e2a3 PR3.

Mirrors ``GET /api/v1/agents`` + ``/api/v1/agents/me`` for the CLI.
Distinct from ``map persona ...`` which manages the local
``.map/agents.yaml`` identity config.

Commands:
* ``map agent show`` — current MAP agent identity (== ``map me``)
* ``map agent list`` — agents visible to the caller
* ``map agent escalation-target`` — STATE_MACHINE.* error escalation
  contact resolver (I1(c))

The user-facing ``map persona list`` / ``map persona whoami`` path is
unchanged — this sub-app is additive.
"""
from __future__ import annotations

import uuid
from typing import Any

import typer

from cli import runner  # module ref: test monkeypatch surface (T23)

agent_app = typer.Typer(help="Agent commands (server-side identity)")


def _parse_uuid(raw: str | None, *, field: str) -> uuid.UUID | None:
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise typer.BadParameter(f"{field} must be a UUID, got {raw!r}") from exc


@agent_app.command("show")
def agent_show() -> None:
    """Show the current MAP agent identity (== ``map me`` / ``map persona whoami``)."""

    def action(c: Any) -> Any:
        return c.get_me()

    runner._run(action)


@agent_app.command("list")
def agent_list(
    project_id: str | None = typer.Option(None, "--project-id", help="Filter by project UUID."),
    role: str | None = typer.Option(
        None, "--role", help="Filter by role (admin | agent)."
    ),
) -> None:
    """List agents visible to the caller.

    Admins see every agent. Project-bound agents see admins and agents
    in their own project. Use ``--project-id`` / ``--role`` to narrow.
    """

    pid = _parse_uuid(project_id, field="project_id")

    def action(c: Any) -> Any:
        return c.list_agents(project_id=pid, role=role)

    runner._run(action)


@agent_app.command("escalation-target")
def agent_escalation_target(
    experiment_id: str | None = typer.Option(
        None,
        "--experiment-id",
        help="Experiment UUID — its escalation_target_agent_id override takes precedence.",
    ),
) -> None:
    """Resolve the escalation contact for a STATE_MACHINE.* error.

    Server runs the 3-tier rule (override → caller → same-role → admin)
    and returns the chosen agent + the tier that picked it. CLI uses
    this on ``MAPHTTPError`` to append ``Escalation: @<name>`` to the
    error output.
    """

    eid = _parse_uuid(experiment_id, field="experiment_id")

    def action(c: Any) -> Any:
        return c.get_escalation_target(experiment_id=eid)

    runner._run(action)


@agent_app.command("register")
def agent_register(
    name: str = typer.Option(..., "--name", help="Unique agent name (1..128 chars)."),
    role: str = typer.Option("agent", "--role", help="admin | agent (default agent)."),
    project_key: str | None = typer.Option(
        None, "--project-key", help="Project key for role=agent (mutually exclusive with --project-id)."
    ),
    project_id: str | None = typer.Option(
        None, "--project-id", help="Project UUID for role=agent (mutually exclusive with --project-key)."
    ),
) -> None:
    """Register a new agent (admin-only; role=agent requires a project)."""

    pid = _parse_uuid(project_id, field="project_id")

    def action(c: Any) -> Any:
        return c.register_agent(
            name=name,
            role=role,
            project_key=project_key,
            project_id=pid,
        )

    runner._run(action, admin=True)
