"""``map host ...`` sub-app — host-orchestrated agent invocation.

Lets the host agent directly invoke participant/reviewer agents and get
synchronous responses, without relying on simple-waker polling.

Usage::

    map --persona host host invoke --persona participant --prompt "..."
    map --persona host host invoke --persona reviewer --prompt-file ./review-task.md
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from map_types.persona import pick_agent_by_persona

from cli.orchestrator import InvokeResult

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


def _render_stream_event(event: dict[str, Any]) -> None:
    """Render a streamed WakeUpEvent to stderr for ``--follow`` (A2)."""
    etype = event.get("type")
    if etype == "text":
        typer.echo(str(event.get("content") or ""), err=True)
    elif etype == "tool_use":
        name = event.get("name") or event.get("tool_name") or "tool"
        typer.echo(f"[tool:{name}]", err=True)
    elif etype == "tool_result":
        typer.echo("[tool-result]", err=True)


def _dispatch_cancel_notification(
    persona: str,
    result: InvokeResult,
    project_root: Path | None,
) -> None:
    """A1: 向 timeout 被调用方发取消 notification(wakeable 通道)。

    语义是「通知对方会话已不被等待」而非静默杀进程 —— 被调方 session 仍挂
    在其 persona 侧,收到通知后自行决定收尾(plan A1 + participant 修正)。
    """
    from map_client.project_config import resolve_client

    try:
        client = resolve_client(persona="host", project_root=project_root)
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"[orchestrator] 取消通知未发送(无法构造 host 客户端): {exc}", err=True)
        return
    try:
        me = client.get_me()
        agents = client.list_agents(project_id=me.project_id)
        target = pick_agent_by_persona(
            agents,
            persona,
            project_key=getattr(me, "project_key", None),
        )
        if target is None:
            typer.echo(
                f"[orchestrator] 取消通知未发送(persona '{persona}' 无匹配 agent)",
                err=True,
            )
            return
        client.dispatch_notification(
            recipient_agent_id=target.id,
            event="host.invoke.cancelled",
            summary=(
                f"host invoke 等待超过 {result.waited_seconds:g}s 已取消;"
                f"目标 session 状态={result.session_state} —— 该会话可能仍在运行"
            ),
            target_type="experiment",
            wakeable=True,
        )
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"[orchestrator] 取消通知发送失败: {exc}", err=True)
        return
    typer.echo(f"[orchestrator] 已向 {target.name} 发送取消通知", err=True)


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
    timeout: float | None = typer.Option(
        None,
        "--timeout",
        help="Wait ceiling in seconds; on expiry print a friendly error (no stack) "
        "and dispatch a cancel notification to the target persona",
    ),
    follow: bool = typer.Option(
        False,
        "--follow",
        help="Stream live progress (text / tool events) to stderr; stdout still "
        "carries only the final response",
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

        # Live-stream progress to stderr; cut off after 60s with a friendly
        # error plus a cancel notification to the target persona
        map --persona host host invoke --persona participant \\
            --prompt "..." --follow --timeout 60
    """
    import json as json_lib

    from cli.main import _cli_options  # runtime state (monkeypatch surface)
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
        timeout=timeout,
        follow=follow,
        on_stream=_render_stream_event if follow else None,
    )

    if result.timed_out:
        # A1: 到点友好报错(无堆栈)+ 向被调方发取消通知(wakeable 通道)
        if json_output:
            typer.echo(
                json_lib.dumps(
                    {
                        "persona": result.persona,
                        "status": "timeout",
                        "response": result.response_text,
                        "session_id": result.session_id,
                        "session_state": result.session_state,
                        "waited_seconds": result.waited_seconds,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            typer.echo(f"Error: {result.error}", err=True)
        _dispatch_cancel_notification(
            persona,
            result,
            project_root or _cli_options.get("project_root"),
        )
        raise typer.Exit(1)

    if json_output:
        typer.echo(
            json_lib.dumps(
                {
                    "persona": result.persona,
                    "status": result.status,
                    "response": result.response_text,
                    "session_id": result.session_id,
                    "session_state": result.session_state,
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
        f"session={result.session_id or '<none>'} "
        f"state={result.session_state or '<unknown>'}",
        err=True,
    )
