"""``map sync ...`` sub-app — local cache sync commands.

Commands:
    map sync pull       — Pull project data from remote server to local cache
    map sync status     — Show sync status (last pull time, cached counts)
    map sync topics     — List cached topics (offline, from local cache)
    map sync topic      — Show a cached topic by ID (offline, from local cache)
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import typer
from map_client.client import MAPClient

sync_app = typer.Typer(help="Local cache sync commands (offline browsing)")


def _require_cache_session():
    """Open the local cache DB, exiting with a helpful hint if .map/ is missing."""
    from cli.main import find_map_dir, _cli_options
    from cli.local_cache import init_cache, get_cache_path

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
    from cli.main import _resolve_project, _run
    from cli.local_cache import pull_project_to_cache

    map_dir, db_path, session = _require_cache_session()

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
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
        _run(action)
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
