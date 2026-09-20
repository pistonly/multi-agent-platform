"""``map runtime ...`` sub-app — cli/main.py split.

Command bodies lazy-import ``cli.main`` helpers to break the import cycle.
"""
from __future__ import annotations

from pathlib import Path

import typer

from cli.runner import _print_json  # noqa: E402

runtime_app = typer.Typer(help="Agent runtime session commands")


@runtime_app.command("check")
def runtime_check(
    persona: str = typer.Option("host", "--persona", "-p"),
    project_root: Path | None = typer.Option(None, "--project-root"),
    env_file: Path | None = typer.Option(None, "--env-file", help="Explicit shared Claude env file"),
    model: str | None = typer.Option(None, "--model"),
    effort: str | None = typer.Option(None, "--effort"),
) -> None:
    """Inspect Claude configuration locally, without credentials or a model call.

    Checks presence only; it does not verify gateway access or account validity.
    Uses the same configuration resolver as host invoke, chat and simple-waker.
    """
    from importlib.util import find_spec

    from cli.agent_client import PersonaAgentClient
    from cli.main import _cli_options
    from cli.runtime_chat import default_runtime_home, resolve_project_root

    root = resolve_project_root(project_root or _cli_options.get("project_root"))
    try:
        agent = PersonaAgentClient(
            persona=persona, state={}, save_state_fn=lambda: None,
            project_root=root, env_file=env_file, model=model, effort=effort,
        )
    except ValueError as exc:
        _print_json({"status": "error", "error": str(exc)})
        raise typer.Exit(1) from None
    env = agent._resolve_env()
    credential_keys = [key for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN") if env.get(key)]
    cached_login = (default_runtime_home(root, persona) / ".claude" / ".credentials.json").is_file()
    sdk_available = find_spec("claude_agent_sdk") is not None
    issues = []
    if not sdk_available:
        issues.append('Install the runtime SDK: pip install "multi-agent-platform[claude-runtime]"')
    if not credential_keys and not cached_login:
        issues.append(
            "No Claude credentials found for the isolated persona runtime. "
            "Create .map/.claude-env or select an existing file with --env-file / MAP_CLAUDE_ENV_FILE. "
            "The MAP API token and your shell's interactive Claude login are separate."
        )
    _print_json({
        "status": "configured" if not issues else "needs_configuration",
        "project_root": str(root),
        "persona": persona,
        "env_file": str(agent.env_file) if agent.env_file else None,
        "source": "env_file" if agent.env_file else "process_env_or_shell_rc",
        "model": agent.model,
        "effort": agent.effort,
        "credential_keys": credential_keys,
        "cached_login_present": cached_login,
        "custom_endpoint_configured": bool(env.get("ANTHROPIC_BASE_URL")),
        "sdk_available": sdk_available,
        "gateway_verified": False,
        "issues": issues,
    })
    if issues:
        raise typer.Exit(1)


@runtime_app.command("chat")
def runtime_chat(
    persona: str = typer.Option(
        "host",
        "--persona",
        "-p",
        help="Persona whose runtime session to resume (default: host)",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="Repo root containing .map/ (default: search upward from cwd)",
    ),
    state_file: Path | None = typer.Option(
        None,
        "--state-file",
        help="Runtime waker state file (default: .map/runtime-waker-state-<persona>.json)",
    ),
    runtime_home: Path | None = typer.Option(
        None,
        "--runtime-home",
        help="Claude runtime HOME (default: .map/claude-runtime-home-<persona>)",
    ),
    session_id: str | None = typer.Option(
        None,
        "--session-id",
        help="Resume a specific Claude session id (default: read from state file)",
    ),
    prompt: str | None = typer.Option(
        None,
        "--prompt",
        help="Send one prompt before entering interactive REPL",
    ),
    new_session: bool = typer.Option(
        False,
        "--new-session",
        help="Start a fresh Claude session instead of resuming state",
    ),
    ignore_waker: bool = typer.Option(
        False,
        "--ignore-waker",
        help="Allow chat while simple-waker is running (may conflict)",
    ),
    model: str | None = typer.Option(None, "--model", help="Optional Claude model override"),
) -> None:
    """Resume a persona runtime session and chat interactively from the terminal."""
    from cli.main import _cli_options  # runtime state (monkeypatch surface)
    from cli.runtime_chat import run_runtime_chat

    run_runtime_chat(
        persona=persona,
        project_root=project_root or _cli_options.get("project_root"),
        state_file=state_file,
        runtime_home=runtime_home,
        session_id=session_id,
        new_session=new_session,
        initial_prompt=prompt,
        ignore_waker=ignore_waker,
        model=model,
    )


@runtime_app.command("status")
def runtime_status(
    persona: str = typer.Option(
        "host",
        "--persona",
        "-p",
        help="Persona to inspect (default: host)",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="Repo root containing .map/ (default: search upward from cwd)",
    ),
    state_file: Path | None = typer.Option(
        None,
        "--state-file",
        help="Runtime waker state file (default: .map/runtime-waker-state-<persona>.json)",
    ),
) -> None:
    """Show resumable session id and whether runtime waker is running."""
    from cli.main import _cli_options  # runtime state (monkeypatch surface)
    from cli.runtime_chat import dump_runtime_chat_status

    _print_json(
        dump_runtime_chat_status(
            persona=persona,
            project_root=project_root or _cli_options.get("project_root"),
            state_file=state_file,
        )
    )
