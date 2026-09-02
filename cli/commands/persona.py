"""``map persona ...`` sub-app — cli/main.py split.

Local ``.map/agents.yaml`` identity commands.
Command bodies lazy-import ``cli.main`` helpers to break the import cycle.
"""
from __future__ import annotations

from pathlib import Path

import typer
from map_client.client import MAPClient
from map_client.project_config import load_project_map_config

from cli import runner  # module ref: test monkeypatch surface (T23)
from cli.runner import _print_json, _require_map_dir  # noqa: E402

persona_app = typer.Typer(help="Persona / identity commands")


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
    from cli.main import _cli_options  # runtime state (monkeypatch surface)
    if persona is not None:
        _cli_options["persona"] = persona
    if project_root is not None:
        _cli_options["project_root"] = project_root

    from cli.commands.doctor import warn_config_divergence

    if _cli_options.get("format") in (None, "yaml"):
        warn_config_divergence(project_root=_cli_options.get("project_root"))

    def action(c: MAPClient):
        me = c.get_me()
        payload = me.model_dump(mode="json")
        if _cli_options.get("persona"):
            payload["persona"] = _cli_options["persona"]
        else:
            from cli.project_context import optional_context

            context = optional_context()
            if context is not None:
                payload["persona"] = context.config.default_persona
        return payload

    runner._run(action)

