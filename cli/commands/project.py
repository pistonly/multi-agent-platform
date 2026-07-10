"""``map project ...`` sub-app — cli/main.py split.

Command bodies lazy-import ``cli.main`` helpers to break the import cycle.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import typer
from map_client.client import MAPClient
from server.domain.schemas import ProjectStatusRevise

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

