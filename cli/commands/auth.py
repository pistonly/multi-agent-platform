"""``map auth ...`` sub-app — self-service credential recovery (M52C).

``map auth reissue`` recovers a lost agent token using the project_key
(same self-service trust boundary as ``map bootstrap``): the server
rotates the token, revoking the old one, and the CLI writes the new
token back into ``.map/agents.local.yaml``.
"""
from __future__ import annotations

from pathlib import Path

import typer
from map_client.bootstrap import reissue_map_token
from map_client.exceptions import MAPHTTPError

auth_app = typer.Typer(help="Manage MAP credentials (token recovery)")


@auth_app.command("reissue")
def auth_reissue(
    name: str = typer.Option(
        ...,
        "--name",
        "-n",
        help="Agent name to reissue (e.g. multi-agent-platform-host; "
        "see .map/agents.yaml).",
    ),
    key: str | None = typer.Option(
        None,
        "--key",
        "-k",
        help="Project key (default: project_key from .map/config.yaml).",
    ),
    api_url: str | None = typer.Option(
        None,
        "--api-url",
        help="MAP API base URL (default: api_url from .map/config.yaml, "
        "then MAP_API_URL, then http://localhost:18400).",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="Directory containing .map/ (default: current directory).",
    ),
    token: str | None = typer.Option(
        None,
        "--token",
        "-t",
        help="Bearer token for the reissue call (explicit, or falls back to "
        "MAP_ADMIN_TOKEN, then any surviving token in .map/agents.local.yaml).",
    ),
) -> None:
    """Reissue an agent's API token and write it back to .map/agents.local.yaml.

    Recovery path for a lost ``.map/agents.local.yaml``. The previous
    token is revoked immediately (anywhere it was used will start
    returning 401 until re-reissued). Authorization requires a valid token:
    an admin token may reissue any agent; a persona token may only reissue
    its own agent (a sibling persona's token is NOT enough — use
    MAP_ADMIN_TOKEN / ~/.map/admin.yaml for cross-persona recovery).

    \b
    Examples:
        map auth reissue --key my-project --name my-project-host
        map auth reissue --name my-project-host          # from .map/config.yaml
    """
    from cli.main import _cli_options

    fmt = _cli_options.get("format", "yaml")
    root = project_root or _cli_options.get("project_root")

    try:
        result = reissue_map_token(
            agent_name=name,
            project_key=key,
            project_root=Path(root) if root else None,
            api_url=api_url,
            bearer_token=token,
        )
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except MAPHTTPError as exc:
        typer.echo(f"Error {exc.status_code}: {exc.detail}", err=True)
        raise typer.Exit(1) from exc

    if fmt == "json":
        import json

        typer.echo(
            json.dumps(
                {
                    "ok": True,
                    "data": {
                        "agent_id": result.agent_id,
                        "agent_name": result.agent_name,
                        "persona": result.persona_key,
                        "api_url": result.api_url,
                        "wrote_back": str(result.local_path),
                        "previous_token_revoked": True,
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    typer.echo(f"Reissued token for agent '{result.agent_name}' ({result.agent_id})")
    typer.echo(f"Previous token revoked. Wrote back: {result.local_path}")
    if result.persona_key:
        typer.echo(f"Try: map --persona {result.persona_key} persona whoami")
    else:
        typer.echo("Try: map persona whoami")
