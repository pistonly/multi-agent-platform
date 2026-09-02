"""Interactive MAP runtime chat — resume a persona session from the terminal.

Unlike simple-waker (one prompt per wake), ``map runtime chat`` keeps the
Claude SDK connection open and accepts multiple user turns. Manual mode does not
call the D6 ``inbound-event record`` server gate.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer

from cli.agent_client import PersonaAgentClient, WakeUpEvent
from cli.bridge_state import load_bridge_state, save_bridge_state
from cli.errors import WorkerError
from cli.wake_backend import sync_runtime_skills

DEFAULT_STATE_TEMPLATE = ".map/runtime-waker-state-{persona}.json"
DEFAULT_RUNTIME_HOME_TEMPLATE = ".map/claude-runtime-home-{persona}"

_REPL_HELP = """\
Commands:
  /help, /?     Show this help
  /session      Print resumed Claude session id
  /quit, /exit  End chat (Ctrl+D also quits)
"""


def resolve_project_root(project_root: Path | None) -> Path:
    """实验 e7244a91（A1）：workspace 经 ProjectContext 单点解析。

    解析不出（无 .map/config.yaml）时给出 bootstrap 指引并退出——此前
    map_dir 为 None 会以 AttributeError 裸崩。
    """
    import typer as _typer

    from cli.project_context import ProjectRootNotFoundError, current_context

    if project_root is not None:
        return project_root.resolve()
    try:
        return current_context().workspace_root.resolve()
    except ProjectRootNotFoundError as exc:
        _typer.echo(f"Error: {exc}", err=True)
        raise SystemExit(1) from exc


def default_state_file(project_root: Path, persona: str) -> Path:
    return project_root / DEFAULT_STATE_TEMPLATE.format(persona=persona)


def default_runtime_home(project_root: Path, persona: str) -> Path:
    return project_root / DEFAULT_RUNTIME_HOME_TEMPLATE.format(persona=persona)


def load_runtime_state(state_file: Path) -> dict[str, Any]:
    return load_bridge_state(
        state_file,
        bridge_name="runtime-waker",
        default_collections=("personas",),
    )


def persona_agent_state(state: dict[str, Any], persona: str) -> dict[str, Any]:
    personas = state.setdefault("personas", {})
    if not isinstance(personas, dict):
        personas = {}
        state["personas"] = personas
    persona_state = personas.setdefault(persona, {})
    if not isinstance(persona_state, dict):
        persona_state = {}
        personas[persona] = persona_state
    return persona_state


def resolve_session_id(
    persona_state: dict[str, Any],
    *,
    session_id: str | None,
    new_session: bool,
) -> str | None:
    if new_session:
        return None
    if session_id:
        return session_id
    for key in ("claude_session_id", "runtime_session_id"):
        value = persona_state.get(key)
        if value:
            return str(value)
    return None


def find_runtime_waker_pids(persona: str) -> list[int]:
    # simple-waker 是默认 waker，实际进程是
    # `python3 -m cli.simple_waker --persona <persona>`（legacy runtime-waker
    # SSE 路径已下线）。
    pattern = rf"cli\.simple_waker.*--persona {persona}"
    result = subprocess.run(
        ["pgrep", "-f", pattern],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return pids


def ensure_waker_not_running(*, persona: str, ignore_waker: bool) -> None:
    if ignore_waker:
        return
    pids = find_runtime_waker_pids(persona)
    if not pids:
        return
    pid_list = ", ".join(str(pid) for pid in pids)
    raise WorkerError(
        f"runtime waker for persona={persona!r} is already running (pid(s): {pid_list}). "
        "Stop it before `map runtime chat` to avoid session conflicts, or pass --ignore-waker."
    )


def format_session_banner(
    *,
    persona: str,
    session_id: str | None,
    state_file: Path,
    runtime_home: Path,
    resumed: bool,
) -> str:
    session_label = session_id or "<new session>"
    mode = "resume" if resumed else "new"
    return (
        f"MAP runtime chat · persona={persona} · {mode} · session={session_label}\n"
        f"state={state_file}\n"
        f"runtime_home={runtime_home}\n"
        "Type /help for commands. Empty line is ignored.\n"
    )


def _default_on_event(event: WakeUpEvent) -> None:
    if event.get("type") == "text":
        sys.stdout.write(str(event.get("content") or ""))
        sys.stdout.flush()


async def run_chat_loop(
    client: PersonaAgentClient,
    *,
    initial_prompt: str | None,
    input_fn: Callable[[str], str] = input,
    on_event: Callable[[WakeUpEvent], None] | None = None,
) -> None:
    emit = on_event or _default_on_event

    async def turn(prompt: str) -> str:
        return await client.wake_up(
            prompt,
            on_event=emit,
            event_source="manual",
        )

    await client.connect()

    if initial_prompt:
        typer.echo(f"\n[prompt] {initial_prompt}\n")
        status = await turn(initial_prompt)
        if status == "error":
            raise WorkerError("Claude returned error status for initial prompt")
        typer.echo("\n")

    while True:
        try:
            line = input_fn("map> ").strip()
        except EOFError:
            typer.echo("")
            break
        if not line:
            continue
        lowered = line.lower()
        if lowered in {"/quit", "/exit", "/q"}:
            break
        if lowered in {"/help", "/?"}:
            typer.echo(_REPL_HELP)
            continue
        if lowered == "/session":
            sid = client.state.get("claude_session_id") or client.state.get("runtime_session_id")
            typer.echo(sid or "<none>")
            continue

        typer.echo("")
        status = await turn(line)
        if status == "error":
            typer.echo("\n[error] Claude returned error status for this turn.", err=True)
        typer.echo("\n")

    await client.disconnect()


async def run_runtime_chat_async(
    *,
    persona: str,
    project_root: Path | None,
    state_file: Path | None,
    runtime_home: Path | None,
    session_id: str | None,
    new_session: bool,
    initial_prompt: str | None,
    ignore_waker: bool,
    model: str | None,
) -> None:
    root = resolve_project_root(project_root)
    resolved_state_file = (state_file or default_state_file(root, persona)).resolve()
    resolved_runtime_home = (runtime_home or default_runtime_home(root, persona)).resolve()

    ensure_waker_not_running(persona=persona, ignore_waker=ignore_waker)

    state = load_runtime_state(resolved_state_file)
    agent_state = persona_agent_state(state, persona)
    resume_id = resolve_session_id(agent_state, session_id=session_id, new_session=new_session)
    if new_session:
        agent_state.pop("claude_session_id", None)
        agent_state.pop("runtime_session_id", None)

    _synced, _skipped = sync_runtime_skills(  # noqa: F841 — kept for parity with simple_waker startup audit
        project_root=root, runtime_home=resolved_runtime_home
    )
    resolved_runtime_home.mkdir(parents=True, exist_ok=True)

    def save_state() -> None:
        save_bridge_state(resolved_state_file, state)

    extra_env = {
        "HOME": str(resolved_runtime_home),
        "MAP_RUNTIME_CHAT_PERSONA": persona,
    }

    client = PersonaAgentClient(
        persona=persona,
        state=agent_state,
        save_state_fn=save_state,
        project_root=root,
        extra_env=extra_env,
        model=model,
        integration="manual",
    )

    if resume_id and not new_session:
        client.state["claude_session_id"] = resume_id
        client.state["runtime_session_id"] = resume_id

    typer.echo(
        format_session_banner(
            persona=persona,
            session_id=resume_id,
            state_file=resolved_state_file,
            runtime_home=resolved_runtime_home,
            resumed=bool(resume_id),
        )
    )

    try:
        await run_chat_loop(client, initial_prompt=initial_prompt)
    finally:
        save_state()


def run_runtime_chat(**kwargs: Any) -> None:
    try:
        asyncio.run(run_runtime_chat_async(**kwargs))
    except WorkerError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        typer.echo("\nInterrupted.")
        raise typer.Exit(130) from None


def dump_runtime_chat_status(
    *,
    persona: str,
    project_root: Path | None,
    state_file: Path | None,
) -> dict[str, Any]:
    root = resolve_project_root(project_root)
    resolved_state_file = (state_file or default_state_file(root, persona)).resolve()
    state = load_runtime_state(resolved_state_file)
    agent_state = persona_agent_state(state, persona)
    session_id = resolve_session_id(agent_state, session_id=None, new_session=False)
    return {
        "persona": persona,
        "project_root": str(root),
        "state_file": str(resolved_state_file),
        "runtime_home": str(default_runtime_home(root, persona)),
        "session_id": session_id,
        "waker_pids": find_runtime_waker_pids(persona),
        "last_wakeup_at": agent_state.get("last_wakeup_at"),
        "last_wakeup_status": agent_state.get("last_wakeup_status"),
    }


__all__ = [
    "default_runtime_home",
    "default_state_file",
    "dump_runtime_chat_status",
    "ensure_waker_not_running",
    "find_runtime_waker_pids",
    "format_session_banner",
    "load_runtime_state",
    "persona_agent_state",
    "resolve_project_root",
    "resolve_session_id",
    "run_chat_loop",
    "run_runtime_chat",
    "run_runtime_chat_async",
]
