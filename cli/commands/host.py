"""``map host ...`` sub-app — host-orchestrated agent invocation.

Lets the host agent directly invoke participant/reviewer agents and get
synchronous responses, without relying on simple-waker polling.

Usage::

    map --persona host host invoke --persona participant --prompt "..."
    map --persona host host invoke --persona reviewer --prompt-file ./review-task.md
"""
from __future__ import annotations

from pathlib import Path

import typer

host_app = typer.Typer(help="Host-only orchestration commands (invoke other personas)")


@host_app.callback()
def host_callback() -> None:
    """Host-only orchestration commands.

    Lets the host agent directly invoke participant/reviewer agents
    and get synchronous responses, without relying on simple-waker polling.
    """
    # Callback exists to force multi-command mode (so `map host invoke`
    # is required rather than `map host` directly invoking the single command).
    return


@host_app.command("invoke")
def host_invoke(
    persona: str = typer.Option(
        ...,
        "--persona",
        "-p",
        help="Target persona to invoke (participant or reviewer)",
    ),
    prompt: str | None = typer.Option(
        None,
        "--prompt",
        help="Prompt text to send to the target persona",
    ),
    prompt_file: Path | None = typer.Option(
        None,
        "--prompt-file",
        help="Read prompt from file (overrides --prompt)",
    ),
    new_session: bool = typer.Option(
        False,
        "--new-session",
        help="Start a fresh Claude session instead of resuming",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        help="Optional Claude model override",
    ),
    ignore_waker: bool = typer.Option(
        False,
        "--ignore-waker",
        help="Allow invoking even if simple-waker is running for this persona",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="Repo root containing .map/ (default: search upward from cwd)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output result as JSON (response + metadata) instead of plain text",
    ),
) -> None:
    """Invoke another persona agent (participant/reviewer) from host.

    The host agent constructs a prompt describing what the target persona
    should do (e.g., comment on a topic, review an experiment). The target
    persona agent processes the prompt using its own Claude session and
    Skills, and the response is printed to stdout.

    Examples::

        # Ask participant to comment on a topic
        map --persona host host invoke --persona participant \\
            --prompt "请参与话题 <topic-id> 的讨论，发表你的观点"

        # Ask reviewer to review an experiment plan
        map --persona host host invoke --persona reviewer \\
            --prompt "请评审实验 <experiment-id> 的计划，提交结构化评审"

        # Read prompt from file
        map --persona host host invoke --persona participant \\
            --prompt-file ./task.md
    """
    import json as json_lib

    from cli.main import _cli_options
    from cli.orchestrator import run_invoke

    # Resolve prompt
    if prompt_file is not None:
        actual_prompt = prompt_file.read_text(encoding="utf-8")
    elif prompt is not None:
        actual_prompt = prompt
    else:
        typer.echo("Error: either --prompt or --prompt-file is required", err=True)
        raise typer.Exit(1)

    result = run_invoke(
        persona=persona,
        prompt=actual_prompt,
        project_root=project_root or _cli_options.get("project_root"),
        new_session=new_session,
        model=model,
        ignore_waker=ignore_waker,
    )

    if json_output:
        typer.echo(
            json_lib.dumps(
                {
                    "persona": result.persona,
                    "status": result.status,
                    "response": result.response_text,
                    "session_id": result.session_id,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if result.status == "error":
        typer.echo(
            f"Error: agent '{persona}' returned error status"
            + (f": {result.error}" if result.error else ""),
            err=True,
        )
        raise typer.Exit(1)

    if result.response_text:
        typer.echo(result.response_text)

    typer.echo(
        f"\n[orchestrator] persona={result.persona} status={result.status} "
        f"session={result.session_id or '<none>'}",
        err=True,
    )
