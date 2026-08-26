"""``map sync ...`` sub-app — local cache + map/ folder projection sync.

Commands:
    map sync pull       — Pull project data from remote server to local cache
    map sync status     — Show cache sync status (last pull time, cached counts)
    map sync topics     — List cached topics (offline, from local cache)
    map sync topic      — Show a cached topic by ID (offline, from local cache)
    map sync publish    — Publish local map/ folders to the server projection
    map sync diff       — Compare local map/ with the server projection
    map sync check      — Folder-plane handshake (local hash + server revision)
    map sync push       — Deprecated alias of ``map sync publish --full``
"""
from __future__ import annotations

import json
import uuid

import typer
from map_client.client import MAPClient
from map_client.project_config import find_map_dir  # noqa: E402

from cli import runner  # module ref: test monkeypatch surface (T23)

sync_app = typer.Typer(
    help=(
        "Local cache (pull/status/topics) and map/ folder projection "
        "(publish/diff/check)."
    )
)


def _require_cache_session():
    """Open the local cache DB, exiting with a helpful hint if .map/ is missing."""
    from cli.local_cache import get_cache_path, init_cache
    from cli.main import _cli_options  # runtime state (monkeypatch surface)

    map_dir = find_map_dir(_cli_options.get("project_root"))
    if map_dir is None:
        typer.echo("Error: .map/ directory not found. Run `map bootstrap` first.", err=True)
        raise typer.Exit(1)
    db_path = get_cache_path(map_dir)
    return map_dir, db_path, init_cache(db_path)


@sync_app.command("pull")
def sync_pull(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    include_archived: bool = typer.Option(
        True,
        "--include-archived/--no-archived",
        help="Include archived items (default: yes)",
    ),
) -> None:
    """Pull project data from remote server into local cache (.map/cache.db).

    After pulling, you can browse topics and experiments offline using
    `map sync topics` and `map sync topic --id <uuid>`.
    """
    from cli.local_cache import pull_project_to_cache

    map_dir, db_path, session = _require_cache_session()

    def action(c: MAPClient):
        pid = runner._resolve_project(c, project, project_key)
        # Resolve project_key for sync_meta bookkeeping
        from map_client.config import load_config

        try:
            pk = project_key or load_config().get("project_key") or ""
        except Exception:
            pk = project_key or ""

        result = pull_project_to_cache(
            session, c, pid, pk, include_archived=include_archived,
        )
        typer.echo(
            f"Pulled {result['topics']} topic(s) and {result['experiments']} experiment(s) "
            f"to {db_path}"
        )
        return None

    try:
        runner._run(action)
    finally:
        session.close()


@sync_app.command("status")
def sync_status(
    project: uuid.UUID | None = typer.Option(None, "--project"),
) -> None:
    """Show local cache sync status."""
    from cli.local_cache import get_sync_status

    _, db_path, session = _require_cache_session()
    if not db_path.exists():
        typer.echo("No local cache found. Run `map sync pull` first.")
        return

    try:
        statuses = get_sync_status(session, project)
        if not statuses:
            typer.echo("No sync records found. Run `map sync pull` first.")
            return
        for s in statuses:
            typer.echo(f"Project: {s['project_key']} ({s['project_id']})")
            typer.echo(f"  Last pull: {s['last_pull_at']}")
            typer.echo(f"  Topics: {s['topic_count']}, Experiments: {s['experiment_count']}")
    finally:
        session.close()


@sync_app.command("topics")
def sync_topics(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    """List cached topics from local cache (offline)."""
    from cli.local_cache import get_sync_status, list_cached_topics

    _, db_path, session = _require_cache_session()
    if not db_path.exists():
        typer.echo("No local cache found. Run `map sync pull` first.")
        return

    try:
        # If project_id not given, try to find it from sync_meta
        if project is None:
            statuses = get_sync_status(session)
            if not statuses:
                typer.echo("No cached projects. Run `map sync pull` first.")
                return
            # Use first project (or match by project_key)
            if project_key:
                for s in statuses:
                    if s["project_key"] == project_key:
                        project = uuid.UUID(s["project_id"])
                        break
            if project is None and len(statuses) == 1:
                project = uuid.UUID(statuses[0]["project_id"])
            if project is None:
                typer.echo("Multiple projects cached. Specify --project or --project-key.")
                for s in statuses:
                    typer.echo(f"  {s['project_key']} ({s['project_id']})")
                return

        topics = list_cached_topics(session, project)
        if not topics:
            typer.echo("No cached topics. Run `map sync pull` first.")
            return
        typer.echo(f"{'ID':<36} {'Status':<12} {'Title'}")
        typer.echo("-" * 80)
        for t in topics:
            typer.echo(f"{t['id']:<36} {t['status']:<12} {t['title']}")
    finally:
        session.close()


@sync_app.command("topic")
def sync_topic(
    topic_id: uuid.UUID = typer.Option(..., "--id", help="Topic UUID"),
) -> None:
    """Show a cached topic from local cache (offline)."""
    from cli.local_cache import get_cached_topic

    _, db_path, session = _require_cache_session()
    if not db_path.exists():
        typer.echo("No local cache found. Run `map sync pull` first.")
        return

    try:
        topic = get_cached_topic(session, topic_id)
        if topic is None:
            typer.echo(f"Topic {topic_id} not found in local cache.")
            return
        typer.echo(json.dumps(topic["data"], indent=2, default=str, ensure_ascii=False))
    finally:
        session.close()


@sync_app.command("publish")
def sync_publish(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    full: bool = typer.Option(
        False, "--full", help="Upsert every local object, not just the delta"
    ),
    yes: bool = typer.Option(False, "--yes", help="Confirm remote deletes (tombstones)"),
) -> None:
    """Publish local map/ folders to the server projection (CAS delta + explicit deletes)."""
    from cli.commands.fs import fs_sync

    fs_sync(project=project, project_key=project_key, dry_run=dry_run, full=full, yes=yes)


@sync_app.command("diff")
def sync_diff(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    """Compare local map/ with the server projection (summary only, no bodies)."""
    from cli.commands.fs import fs_diff

    fs_diff(project=project, project_key=project_key)


@sync_app.command("check")
def sync_check(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    """Folder-plane handshake: local hash + server reachability."""
    from cli.commands.fs import fs_status

    fs_status(project=project, project_key=project_key)


@sync_app.command("push")
def sync_push(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    yes: bool = typer.Option(False, "--yes", help="Confirm remote deletes (tombstones)"),
) -> None:
    """Deprecated alias for ``map sync publish --full``."""
    from cli.commands.fs import fs_push

    fs_push(project=project, project_key=project_key, yes=yes)
