"""``map runtime ...`` sub-app — cli/main.py split.

Command bodies lazy-import ``cli.main`` helpers to break the import cycle.
"""
from __future__ import annotations

from pathlib import Path

import typer

runtime_app = typer.Typer(help="Agent runtime session commands")


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
    from cli.main import _cli_options  # lazy: avoid cycle
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
    from cli.main import _cli_options, _print_json  # lazy: avoid cycle
    from cli.runtime_chat import dump_runtime_chat_status

    _print_json(
        dump_runtime_chat_status(
            persona=persona,
            project_root=project_root or _cli_options.get("project_root"),
            state_file=state_file,
        )
    )
