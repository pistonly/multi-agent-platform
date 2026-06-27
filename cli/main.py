import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import httpx
import typer
import yaml
from map_client.client import MAPClient
from map_client.exceptions import MAPHTTPError
from map_client.project_config import find_map_dir, load_project_map_config, resolve_client
from map_client.bootstrap import bootstrap_project_map
from server.domain.models import AgentRole
from server.domain.schemas import (
    ExperimentComplete,
    ExperimentCreate,
    ExperimentLogCreate,
    PlanInput,
    PlanRevise,
    ProjectStatusRevise,
    ReviewCreate,
)

app = typer.Typer(name="map", help="Multi-Agent Platform CLI")
project_app = typer.Typer(help="Project commands")
experiment_app = typer.Typer(help="Experiment commands")
persona_app = typer.Typer(help="Persona / identity commands")
app.add_typer(project_app, name="project")
app.add_typer(experiment_app, name="experiment")
app.add_typer(persona_app, name="persona")

_transport: httpx.BaseTransport | None = None
_cli_options: dict[str, Any] = {"persona": None, "project_root": None}


@app.callback()
def cli_global_options(
    persona: str | None = typer.Option(
        None,
        "--persona",
        "-p",
        help="Persona from .map/agents.local.yaml (host, participant, reviewer, …)",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="Code repo root containing .map/ (default: search upward from cwd)",
    ),
) -> None:
    _cli_options["persona"] = persona
    _cli_options["project_root"] = project_root


@contextmanager
def _client_ctx() -> Iterator[MAPClient]:
    try:
        client = resolve_client(
            persona=_cli_options.get("persona"),
            project_root=_cli_options.get("project_root"),
            transport=_transport,
        )
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    try:
        yield client
    finally:
        client.close()


def _print_json(data: Any) -> None:
    if hasattr(data, "model_dump"):
        data = data.model_dump(mode="json")
    typer.echo(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


def _run(action) -> None:
    try:
        with _client_ctx() as client:
            result = action(client)
        if result is not None:
            _print_json(result)
    except MAPHTTPError as exc:
        typer.echo(f"Error {exc.status_code}: {exc.detail}", err=True)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


def _require_map_dir(project_root: Path | None = None) -> Path:
    """Require `.map/config.yaml`; exit with bootstrap hint if missing."""
    root = project_root or _cli_options.get("project_root")
    map_dir = find_map_dir(root)
    if map_dir is None:
        from map_client.project_config import missing_map_config_message

        typer.echo(f"Error: {missing_map_config_message()}", err=True)
        raise typer.Exit(1)
    return map_dir


def _resolve_project(client: MAPClient, project: uuid.UUID | None, project_key: str | None) -> uuid.UUID:
    map_dir = find_map_dir(_cli_options.get("project_root"))
    if map_dir is not None:
        try:
            cfg = load_project_map_config(map_dir=map_dir)
            if project is None and project_key is None:
                return client.get_project_by_key(cfg.project_key).id
        except ValueError:
            pass
    cfg_key = None
    try:
        from map_client.config import load_config

        cfg_key = load_config().get("project_key")
    except Exception:
        pass
    key = project_key or cfg_key
    return client.resolve_project_id(project, project_key=key)


@app.command("bootstrap")
def map_bootstrap(
    key: str = typer.Option(..., "--key", help="MAP project_key for this code repo"),
    name: str | None = typer.Option(None, "--name", help="Human-readable MAP project name"),
    path: Path | None = typer.Option(None, "--path", help="workspace_path stored on MAP project"),
    description: str | None = typer.Option(None, "--description"),
    api_url: str | None = typer.Option(None, "--api-url", help="MAP API base URL"),
    project_root: Path | None = typer.Option(None, "--project-root", help="Where to write .map/"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing .map/agents.local.yaml"),
) -> None:
    """Register MAP project + persona agents; write .map/ config (requires admin token)."""
    root = (project_root or Path.cwd()).resolve()
    workspace = path or root
    display_name = name or key
    try:
        result = bootstrap_project_map(
            project_key=key,
            project_name=display_name,
            workspace_path=workspace,
            project_root=root,
            api_url=api_url,
            description=description,
            force=force,
            transport=_transport,
        )
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except MAPHTTPError as exc:
        typer.echo(f"Error {exc.status_code}: {exc.detail}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Wrote {result.config.map_dir}/")
    typer.echo(f"MAP project_key={result.config.project_key} id={result.config.project_id}")
    if result.created_project:
        typer.echo("Created new MAP project.")
    else:
        typer.echo("Reused existing MAP project.")
    if result.skipped_agent_names:
        typer.echo(
            "Skipped existing agents (tokens not recoverable): "
            + ", ".join(result.skipped_agent_names),
            err=True,
        )
        typer.echo("Keep your existing .map/agents.local.yaml or delete agents on MAP before re-bootstrap.")
    typer.echo("Personas: " + ", ".join(result.config.tokens.keys()))
    typer.echo("Try: map --persona host status")


@persona_app.command("list")
def persona_list(
    project_root: Path | None = typer.Option(None, "--project-root"),
) -> None:
    """List personas defined in .map/agents.yaml."""
    map_dir = _require_map_dir(project_root)
    cfg = load_project_map_config(map_dir=map_dir)
    rows = []
    for key, info in cfg.personas.items():
        has_token = key in cfg.tokens
        rows.append(
            {
                "persona": key,
                "agent_name": info.agent_name,
                "has_token": has_token,
                "description": info.description,
            }
        )
    _print_json(rows)


@persona_app.command("whoami")
def persona_whoami(
    persona: str | None = typer.Option(None, "--persona", "-p"),
    project_root: Path | None = typer.Option(None, "--project-root"),
) -> None:
    """Show MAP identity for the selected persona (default from .map/config.yaml)."""
    if persona is not None:
        _cli_options["persona"] = persona
    if project_root is not None:
        _cli_options["project_root"] = project_root

    def action(c: MAPClient):
        me = c.get_me()
        payload = me.model_dump(mode="json")
        if _cli_options.get("persona"):
            payload["persona"] = _cli_options["persona"]
        elif find_map_dir(_cli_options.get("project_root")):
            payload["persona"] = load_project_map_config(
                project_root=_cli_options.get("project_root")
            ).default_persona
        return payload

    _run(action)


@app.command("me")
def map_me() -> None:
    """Alias for `map persona whoami`."""

    def action(c: MAPClient):
        me = c.get_me()
        payload = me.model_dump(mode="json")
        if _cli_options.get("persona"):
            payload["persona"] = _cli_options["persona"]
        elif find_map_dir(_cli_options.get("project_root")):
            payload["persona"] = load_project_map_config(
                project_root=_cli_options.get("project_root")
            ).default_persona
        return payload

    _run(action)


@app.command("todos")
def map_todos() -> None:
    _run(lambda c: c.get_todos())


@project_app.command("create")
def project_create(
    key: str = typer.Option(..., "--key"),
    name: str = typer.Option(..., "--name"),
    path: str = typer.Option(..., "--path"),
    description: str | None = typer.Option(None, "--description"),
) -> None:
    _run(lambda c: c.create_project(key, name, path, description))


@project_app.command("list")
def project_list(include_archived: bool = typer.Option(False, "--include-archived")) -> None:
    _run(lambda c: c.list_projects(include_archived=include_archived))


status_app = typer.Typer(help="Project Current Status commands")
project_app.add_typer(status_app, name="status")


@status_app.command("revise")
def project_status_revise(
    status_file: Path = typer.Option(..., "--file"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    note: str | None = typer.Option(None, "--note"),
) -> None:
    payload = ProjectStatusRevise(content_md=status_file.read_text(encoding="utf-8"), change_note=note)

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        return c.revise_project_status(pid, payload)

    _run(action)


@status_app.command("versions")
def project_status_versions(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    _run(lambda c: c.list_project_status_versions(_resolve_project(c, project, project_key)))


@status_app.command("show")
def project_status_show(
    version: int = typer.Option(..., "--version"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    _run(
        lambda c: c.get_project_status_version(_resolve_project(c, project, project_key), version)
    )


@experiment_app.command("create")
def experiment_create(
    title: str = typer.Option(..., "--title"),
    plan_file: Path = typer.Option(..., "--plan-file"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    description: str | None = typer.Option(None, "--description"),
    submit_for_review: bool = typer.Option(False, "--submit-for-review"),
    topic_id: uuid.UUID | None = typer.Option(None, "--topic-id"),
) -> None:
    content = plan_file.read_text(encoding="utf-8")
    payload = ExperimentCreate(
        title=title,
        description=description,
        plan=PlanInput(content_md=content),
        submit_for_review=submit_for_review,
        topic_id=topic_id,
    )

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        return c.create_experiment(pid, payload)

    _run(action)


@experiment_app.command("submit-review")
def experiment_submit_review(experiment_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    _run(lambda c: c.submit_for_review(experiment_id))


@experiment_app.command("approve")
def experiment_approve(experiment_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    _run(lambda c: c.approve_experiment(experiment_id))


@experiment_app.command("start")
def experiment_start(experiment_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    _run(lambda c: c.start_experiment(experiment_id))


@experiment_app.command("complete")
def experiment_complete(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    summary: str = typer.Option(..., "--summary"),
    log_file: Path = typer.Option(..., "--file"),
    metadata_file: Path | None = typer.Option(None, "--metadata"),
) -> None:
    metadata = None
    if metadata_file:
        metadata = yaml.safe_load(metadata_file.read_text(encoding="utf-8"))
    payload = ExperimentComplete(
        summary=summary,
        content_md=log_file.read_text(encoding="utf-8"),
        metadata=metadata,
    )
    _run(lambda c: c.complete_experiment(experiment_id, payload))


@experiment_app.command("log")
def experiment_log(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    summary: str = typer.Option(..., "--summary"),
    log_file: Path = typer.Option(..., "--file"),
    metadata_file: Path | None = typer.Option(None, "--metadata"),
) -> None:
    metadata = None
    if metadata_file:
        metadata = yaml.safe_load(metadata_file.read_text(encoding="utf-8"))
    payload = ExperimentLogCreate(
        summary=summary,
        content_md=log_file.read_text(encoding="utf-8"),
        metadata=metadata,
    )
    _run(lambda c: c.create_log(experiment_id, payload))


@experiment_app.command("status")
def experiment_status(experiment_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    _run(lambda c: c.get_experiment(experiment_id))


review_app = typer.Typer(help="Review commands")
experiment_app.add_typer(review_app, name="review")


@review_app.command("add")
def review_add(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    review_file: Path = typer.Option(..., "--review"),
) -> None:
    raw = yaml.safe_load(review_file.read_text(encoding="utf-8"))
    payload = ReviewCreate.model_validate(raw)
    _run(lambda c: c.create_review(experiment_id, payload))


plan_app = typer.Typer(help="Plan commands")
experiment_app.add_typer(plan_app, name="plan")


@plan_app.command("revise")
def plan_revise(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    plan_file: Path = typer.Option(..., "--plan-file"),
    note: str | None = typer.Option(None, "--note"),
) -> None:
    payload = PlanRevise(content_md=plan_file.read_text(encoding="utf-8"), change_note=note)
    _run(lambda c: c.revise_plan(experiment_id, payload))


@experiment_app.command("comment")
def experiment_comment(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    anchor_type: str = typer.Option(..., "--anchor-type"),
    anchor_id: uuid.UUID = typer.Option(..., "--anchor-id"),
    body: str = typer.Option(..., "--body"),
    parent: uuid.UUID | None = typer.Option(None, "--parent"),
) -> None:
    from server.domain.models import CommentAnchorType
    from server.domain.schemas import CommentCreate

    payload = CommentCreate(
        anchor_type=CommentAnchorType(anchor_type),
        anchor_id=anchor_id,
        parent_id=parent,
        body=body,
    )
    _run(lambda c: c.create_comment(experiment_id, payload))


notification_app = typer.Typer(help="In-app notification commands")
app.add_typer(notification_app, name="notification")


@notification_app.command("list")
def notification_list(
    unread_only: bool = typer.Option(False, "--unread-only"),
    limit: int = typer.Option(50, "--limit"),
    offset: int = typer.Option(0, "--offset"),
) -> None:
    _run(lambda c: c.list_notifications(unread_only=unread_only, limit=limit, offset=offset))


@notification_app.command("read")
def notification_read(notification_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    _run(lambda c: c.mark_notification_read(notification_id))


@notification_app.command("read-all")
def notification_read_all() -> None:
    _run(lambda c: c.mark_all_notifications_read())


@app.command("status")
def project_or_global_status(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    def action(c: MAPClient):
        if project is not None or project_key is not None:
            pid = _resolve_project(c, project, project_key)
            return c.get_project_status(pid)
        me = c.get_me()
        if me.role == AgentRole.admin:
            return c.get_global_status()
        pid = _resolve_project(c, None, None)
        return c.get_project_status(pid)

    _run(action)


topic_app = typer.Typer(help="Topic commands")
app.add_typer(topic_app, name="topic")


@topic_app.command("create")
def topic_create(
    title: str = typer.Option(..., "--title"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    description: str | None = typer.Option(None, "--description"),
) -> None:
    from server.domain.schemas import TopicCreate

    payload = TopicCreate(title=title, description=description)

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        return c.create_topic(pid, payload)

    _run(action)


@topic_app.command("list")
def topic_list(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    status: str | None = typer.Option(None, "--status"),
) -> None:
    from server.domain.models import TopicStatus

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        st = TopicStatus(status) if status else None
        return c.list_topics(pid, status=st)

    _run(action)


@topic_app.command("show")
def topic_show(topic_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    _run(lambda c: c.get_topic(topic_id))


@topic_app.command("comment")
def topic_comment(
    topic_id: uuid.UUID = typer.Option(..., "--id"),
    body: str = typer.Option(..., "--body"),
    parent: uuid.UUID | None = typer.Option(None, "--parent"),
) -> None:
    from server.domain.schemas import TopicCommentCreate

    payload = TopicCommentCreate(body=body, parent_id=parent)
    _run(lambda c: c.create_topic_comment(topic_id, payload))


@topic_app.command("close")
def topic_close(topic_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    _run(lambda c: c.close_topic(topic_id))


@topic_app.command("reopen")
def topic_reopen(topic_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    _run(lambda c: c.reopen_topic(topic_id))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
