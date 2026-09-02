"""``map project ...`` sub-app — cli/main.py split.

Command bodies lazy-import ``cli.main`` helpers to break the import cycle.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import typer
from map_client.client import MAPClient
from map_types.schemas import ProjectStatusRevise

from cli import runner  # module ref: test monkeypatch surface (T23)
from cli.io_helpers import _read_text_file  # noqa: E402

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
    runner._run(lambda c: c.create_project(key, name, path, description))


@project_app.command("list")
def project_list(include_archived: bool = typer.Option(False, "--include-archived")) -> None:
    runner._run(lambda c: c.list_projects(include_archived=include_archived))


@project_app.command("decisions")
def project_decisions(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    limit: int = typer.Option(20, "--limit", min=1, max=100),
) -> None:
    def action(c: MAPClient):
        pid = runner._resolve_project(c, project, project_key)
        return c.list_project_decisions(pid, limit=limit)

    runner._run(action)


@status_app.command("revise")
def project_status_revise(
    status_file: Path = typer.Option(..., "--file"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    note: str | None = typer.Option(None, "--note"),
) -> None:
    payload = ProjectStatusRevise(content_md=_read_text_file(status_file, kind="status"), change_note=note)

    def action(c: MAPClient):
        pid = runner._resolve_project(c, project, project_key)
        return c.revise_project_status(pid, payload)

    runner._run(action)


@status_app.command("versions")
def project_status_versions(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    runner._run(lambda c: c.list_project_status_versions(runner._resolve_project(c, project, project_key)))


@status_app.command("show")
def project_status_show(
    version: int = typer.Option(..., "--version"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    runner._run(
        lambda c: c.get_project_status_version(runner._resolve_project(c, project, project_key), version)
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

    Default output is ``.map/history/`` (gitignored runtime dir). Pass
    ``-o`` to a tracked path if the snapshot should be committed.

    \b
    Layout:
        <output_dir>/
          INDEX.md              # Browseable index
          topics/<slug>.md      # One file per topic (with comments + decision)
          experiments/<slug>.md # One file per experiment (with plans + reviews + logs)

    \b
    Examples:
        map project export                      # Export to .map/history/ (local)
        map project export -o ./docs/history    # Tracked output directory
        map project export --no-archived        # Skip archived items
    """
    from cli.project_export import export_project_history

    if output_dir is None:
        from cli.project_context import optional_context

        # 实验 e7244a91（A1）：默认导出到 workspace 的 .map/history/；
        # workspace 解析不出时保持既有相对路径降级。
        context = optional_context()
        output_dir = (
            context.map_dir / "history" if context is not None else Path(".map") / "history"
        )

    def action(c: MAPClient):
        pid = runner._resolve_project(c, project, project_key)
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

    runner._run(action)

