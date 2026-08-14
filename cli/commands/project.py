"""``map project ...`` sub-app — cli/main.py split.

Command bodies lazy-import ``cli.main`` helpers to break the import cycle.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import typer
from map_client.client import MAPClient
from map_types.schemas import ProjectStatusRevise

project_app = typer.Typer(help="Project commands")
status_app = typer.Typer(help="Project Current Status commands")
project_app.add_typer(status_app, name="status")


@project_app.command("create")
def project_create(
    key: str = typer.Option(..., "--key"),
    name: str = typer.Option(..., "--name"),
    path: str = typer.Option(..., "--path"),
    description: str | None = typer.Option(None, "--description"),
) -> None:
    from cli.main import _run  # lazy: avoid cycle
    _run(lambda c: c.create_project(key, name, path, description))


@project_app.command("list")
def project_list(include_archived: bool = typer.Option(False, "--include-archived")) -> None:
    from cli.main import _run  # lazy: avoid cycle
    _run(lambda c: c.list_projects(include_archived=include_archived))


@project_app.command("decisions")
def project_decisions(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    limit: int = typer.Option(20, "--limit", min=1, max=100),
) -> None:
    from cli.main import _resolve_project, _run  # lazy: avoid cycle
    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        return c.list_project_decisions(pid, limit=limit)

    _run(action)


@status_app.command("revise")
def project_status_revise(
    status_file: Path = typer.Option(..., "--file"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    note: str | None = typer.Option(None, "--note"),
) -> None:
    from cli.main import _read_text_file, _resolve_project, _run  # lazy: avoid cycle
    payload = ProjectStatusRevise(content_md=_read_text_file(status_file, kind="status"), change_note=note)

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        return c.revise_project_status(pid, payload)

    _run(action)


@status_app.command("versions")
def project_status_versions(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    from cli.main import _resolve_project, _run  # lazy: avoid cycle
    _run(lambda c: c.list_project_status_versions(_resolve_project(c, project, project_key)))


@status_app.command("show")
def project_status_show(
    version: int = typer.Option(..., "--version"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    from cli.main import _resolve_project, _run  # lazy: avoid cycle
    _run(
        lambda c: c.get_project_status_version(_resolve_project(c, project, project_key), version)
    )


@project_app.command("export")
def project_export(
    output_dir: Path = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="Output directory (default: .map/history/)",
    ),
    include_archived: bool = typer.Option(
        True,
        "--include-archived/--no-archived",
        help="Include archived topics and experiments (default: yes)",
    ),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    """Export project history (topics, experiments, decisions) to local Markdown.

    The export is a read-only snapshot suitable for committing to Git,
    so that topic discussions and experiment records travel with the code.

    \b
    Layout:
        <output_dir>/
          INDEX.md              # Browseable index
          topics/<slug>.md      # One file per topic (with comments + decision)
          experiments/<slug>.md # One file per experiment (with plans + reviews + logs)

    \b
    Examples:
        map project export                      # Export to .map/history/
        map project export -o ./docs/history    # Custom output directory
        map project export --no-archived        # Skip archived items
    """
    from cli.main import _resolve_project, _run  # lazy: avoid cycle
    from cli.project_export import export_project_history

    if output_dir is None:
        from cli.main import _cli_options, find_map_dir
        map_dir = find_map_dir(_cli_options.get("project_root"))
        output_dir = map_dir / "history" if map_dir is not None else Path(".map") / "history"

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        result_dir = export_project_history(
            c,
            pid,
            output_dir,
            include_archived=include_archived,
        )
        # Count files for feedback
        topic_files = list((result_dir / "topics").glob("*.md"))
        exp_files = list((result_dir / "experiments").glob("*.md"))
        typer.echo(
            f"Exported {len(topic_files)} topic(s) and {len(exp_files)} experiment(s) "
            f"to {result_dir}/"
        )
        return None

    _run(action)

