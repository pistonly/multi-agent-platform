"""Host-facing operator CLI for experiment execution lock (CP-2.5).

Subcommand::

    map --persona host experiment force-release-lock --id <exp_id> [--reason "..."]

The CLI is intentionally minimal — it does not depend on the worker, and can be
invoked from a maintenance shell when a host worker has crashed and left a
stale lock.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import typer
import yaml

from cli.experiment_lock import (
    ExperimentLockManager,
    InMemoryLockBackend,
    LOG_FORCE,
    MapClientLockBackend,
)
from cli.host_worker_types import WorkerError
from cli.map_command_client import MapCommandClient

app = typer.Typer(help="Operator overrides for experiment execution lock.")


def _resolve_actor() -> str:
    """Return the operator identity recorded in the audit log."""
    user = os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
    host = os.environ.get("HOSTNAME") or os.environ.get("COMPUTERNAME") or "local"
    return f"{user}@{host}"


def _resolve_manager(client: MapCommandClient | None) -> ExperimentLockManager:
    if client is None:
        return ExperimentLockManager.from_env(InMemoryLockBackend())
    backend = MapClientLockBackend(client)
    return ExperimentLockManager.from_env(backend)


def force_release_lock(
    experiment_id: str = typer.Option(..., "--id", help="Experiment ID whose lock should be released."),
    reason: str = typer.Option("manual override", "--reason", help="Audit reason (required for ops review)."),
    persona: str = typer.Option("host", "--persona", "-p"),
    project_root: Path | None = typer.Option(None, "--project-root"),
    map_cmd: str = typer.Option("map", "--map-cmd"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Force-release an experiment execution lock and emit an audit log."""
    actor = _resolve_actor()
    client = MapCommandClient(map_cmd=map_cmd, persona=persona, project_root=project_root, dry_run=dry_run)
    manager = _resolve_manager(client)

    if dry_run:
        typer.echo(
            f"[dry-run] would force-release-lock experiment={experiment_id} actor={actor} reason={reason!r}"
        )
        return

    try:
        detail = client.experiment_status(experiment_id)
    except Exception as exc:
        raise WorkerError(f"experiment {experiment_id} not found: {exc}") from exc

    phase = str(detail.get("phase") or "")
    if phase != "running":
        raise WorkerError(
            f"experiment {experiment_id} phase={phase!r} != 'running'; refusing to release lock"
        )

    project_id = str(detail.get("project_id") or "")
    if not project_id:
        raise WorkerError(f"experiment {experiment_id} has no project_id")

    state = manager.force_release(project_id=project_id, reason=reason, actor=actor)
    audit = {
        "action": LOG_FORCE,
        "experiment_id": experiment_id,
        "project_id": project_id,
        "reason": reason,
        "actor": actor,
    }
    typer.echo(yaml.safe_dump({"audit": audit, "lock_state": state.__dict__}, allow_unicode=True, sort_keys=False))


def show_lock(
    experiment_id: str = typer.Option(..., "--id", help="Experiment ID to inspect."),
    persona: str = typer.Option("host", "--persona", "-p"),
    project_root: Path | None = typer.Option(None, "--project-root"),
    map_cmd: str = typer.Option("map", "--map-cmd"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Print the current lock state for an experiment."""
    client = MapCommandClient(map_cmd=map_cmd, persona=persona, project_root=project_root, dry_run=dry_run)
    manager = _resolve_manager(client)
    try:
        detail = client.experiment_status(experiment_id)
    except Exception as exc:
        raise WorkerError(f"experiment {experiment_id} not found: {exc}") from exc

    project_id = str(detail.get("project_id") or "")
    state = manager.backend.get_lock_state(project_id) if project_id else None
    payload: dict[str, Any] = {"experiment": detail}
    if state is not None:
        payload["lock_state"] = {
            "is_held": state.is_held,
            "is_expired": state.is_expired(),
            "holder": state.lock_holder_experiment_id,
            "ttl_seconds": state.lock_ttl_seconds,
            "lock_skip_count": state.lock_skip_count,
            "next_attempt_at": state.next_attempt_at,
        }
    typer.echo(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))


def _emit_json(payload: dict[str, Any]) -> None:  # pragma: no cover - reserved for future use
    import json as _json

    typer.echo(_json.dumps(payload, ensure_ascii=False, sort_keys=True))


@app.command("force-release-lock")
def force_release_lock_cmd(
    experiment_id: str = typer.Option(..., "--id"),
    reason: str = typer.Option("manual override", "--reason"),
    persona: str = typer.Option("host", "--persona", "-p"),
    project_root: Path | None = typer.Option(None, "--project-root"),
    map_cmd: str = typer.Option("map", "--map-cmd"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    force_release_lock(
        experiment_id=experiment_id,
        reason=reason,
        persona=persona,
        project_root=project_root,
        map_cmd=map_cmd,
        dry_run=dry_run,
    )


@app.command("show-lock")
def show_lock_cmd(
    experiment_id: str = typer.Option(..., "--id"),
    persona: str = typer.Option("host", "--persona", "-p"),
    project_root: Path | None = typer.Option(None, "--project-root"),
    map_cmd: str = typer.Option("map", "--map-cmd"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    show_lock(
        experiment_id=experiment_id,
        persona=persona,
        project_root=project_root,
        map_cmd=map_cmd,
        dry_run=dry_run,
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
