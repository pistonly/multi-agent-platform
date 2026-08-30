"""Host-orchestrated agent invocation.

Unlike simple-waker (poll-based background reminding), this module lets the
host agent **directly invoke** participant/reviewer agents and get synchronous
responses. The host agent constructs a prompt describing what the target
persona should do, sends it via this orchestrator, and receives the response.

Typical usage::

    from cli.orchestrator import HostOrchestrator, run_invoke

    # Programmatic
    orchestrator = HostOrchestrator(project_root=Path("/repo"))
    result = await orchestrator.invoke("participant", "请参与话题 ... 的讨论")

    # CLI (preferred — host agent calls via Bash tool)
    # map --persona host host invoke --persona participant --prompt "..."

The orchestrator reuses :class:`~cli.agent_client.PersonaAgentClient` for
Claude SDK connection management, state persistence, and skill syncing.
Each invoked persona runs in its own Claude session with its own credentials.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cli.agent_client import PersonaAgentClient, WakeUpEvent
from cli.bridge_state import load_bridge_state, save_bridge_state
from cli.runtime_chat import (
    default_runtime_home,
    default_state_file,
    ensure_waker_not_running,
    persona_agent_state,
    resolve_project_root,
)
from cli.wake_backend import sync_runtime_skills

__all__ = [
    "HostOrchestrator",
    "InvokeResult",
    "run_invoke",
]


@dataclass
class InvokeResult:
    """Result of invoking a persona agent."""

    persona: str
    status: str  # "ok" | "error" | "no_response" | "timeout"
    response_text: str = ""
    session_id: str | None = None
    error: str | None = None
    # I1 (host invoke 可观测性): A3 启动状态行 + A1 timeout 元数据
    session_state: str | None = None  # "waiting-for-session" | "running"
    timed_out: bool = False
    waited_seconds: float | None = None


class HostOrchestrator:
    """Lets the host agent directly invoke other persona agents.

    Each invoked persona runs in its own Claude SDK session with its own
    credentials. The host agent constructs the prompt; the orchestrator
    handles connection, state management, and response capture.

    Parameters
    ----------
    project_root:
        Repository root containing ``.map/``. If ``None``, auto-detected.
    model:
        Optional Claude model override for all invoked personas.
    ignore_waker:
        If ``True``, skip the check that prevents invoking a persona whose
        waker is currently running (may cause session conflicts).
    """

    def __init__(
        self,
        *,
        project_root: Path | None = None,
        model: str | None = None,
        ignore_waker: bool = False,
    ) -> None:
        self.project_root = resolve_project_root(project_root)
        self.model = model
        self.ignore_waker = ignore_waker
        self._clients: dict[str, PersonaAgentClient] = {}
        self._states: dict[str, tuple[Path, dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def invoke(
        self,
        persona: str,
        prompt: str,
        *,
        new_session: bool = False,
        timeout: float | None = None,
        follow: bool = False,
        on_stream: Callable[[dict[str, Any]], None] | None = None,
    ) -> InvokeResult:
        """Invoke a persona agent and return its response.

        Parameters
        ----------
        persona:
            Target persona name (e.g. ``"participant"``, ``"reviewer"``).
        prompt:
            The prompt to send. The host agent should include enough context
            for the target persona to act (topic_id, experiment_id, etc.).
        new_session:
            If ``True``, start a fresh Claude session instead of resuming
            the existing one for this persona.
        timeout:
            If set, abort waiting after this many seconds and return an
            ``InvokeResult`` with ``status="timeout"`` / ``timed_out=True``.
            The target session is *not* killed — the caller decides how to
            notify the orphaned session (see CLI ``--timeout``).
        follow:
            If ``True``, forward each streamed ``WakeUpEvent`` to ``on_stream``
            as it arrives (CLI prints them to stderr for live progress).
        on_stream:
            Callback receiving ``WakeUpEvent`` dicts when ``follow=True``.

        Returns
        -------
        InvokeResult
            Contains the response text, status, session metadata, A3 startup
            session state, and A1 timeout metadata.
        """
        ensure_waker_not_running(persona=persona, ignore_waker=self.ignore_waker)

        client = self._get_or_create_client(persona)

        if new_session:
            client.state.pop("claude_session_id", None)
            client.state.pop("runtime_session_id", None)
            if client._connected:
                await client.disconnect()

        await client.connect()

        # A3: 启动状态行 —— 目标 session 是复用既有会话(running)还是将新建
        # (waiting-for-session)。由 CLI 层打印给用户。
        session_state = (
            "running"
            if client.state.get("claude_session_id")
            else "waiting-for-session"
        )

        response_parts: list[str] = []
        result_session_id: str | None = None

        def on_event(event: WakeUpEvent) -> None:
            nonlocal result_session_id
            if follow and on_stream is not None:
                on_stream(event)
            etype = event.get("type")
            if etype == "text":
                response_parts.append(str(event.get("content") or ""))
            elif etype == "result":
                result_session_id = event.get("session_id") or result_session_id

        async def _run_wake_up() -> str:
            return await client.wake_up(
                prompt,
                on_event=on_event,
                event_source="orchestrator",
            )

        if timeout is not None:
            try:
                status = await asyncio.wait_for(_run_wake_up(), timeout=timeout)
            except asyncio.TimeoutError:
                # A1: 到点不静默杀进程 —— 目标会话仍挂在其 persona 侧,交由
                # CLI 层向对方发取消 notification(wakeable 通道)告知已被放弃
                return InvokeResult(
                    persona=persona,
                    status="timeout",
                    response_text="".join(response_parts),
                    session_id=result_session_id or client.state.get("claude_session_id"),
                    session_state=session_state,
                    timed_out=True,
                    waited_seconds=timeout,
                    error=(
                        f"waiting for '{persona}' exceeded {timeout:g}s and was cancelled; "
                        f"target session state={session_state}"
                    ),
                )
        else:
            status = await _run_wake_up()

        return InvokeResult(
            persona=persona,
            status=status,
            response_text="".join(response_parts),
            session_id=result_session_id or client.state.get("claude_session_id"),
            session_state=session_state,
        )

    async def disconnect_all(self) -> None:
        """Disconnect all managed persona clients."""
        for client in self._clients.values():
            with contextlib.suppress(Exception):
                await client.disconnect()
        self._clients.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_or_create_client(self, persona: str) -> PersonaAgentClient:
        if persona in self._clients:
            return self._clients[persona]

        state_file = default_state_file(self.project_root, persona)
        runtime_home = default_runtime_home(self.project_root, persona)

        state = load_bridge_state(
            state_file,
            bridge_name="runtime-waker",
            default_collections=("personas",),
        )
        agent_state = persona_agent_state(state, persona)

        def save_state() -> None:
            save_bridge_state(state_file, state)

        extra_env = {
            "HOME": str(runtime_home),
            "MAP_RUNTIME_CHAT_PERSONA": persona,
        }

        _synced, _skipped = sync_runtime_skills(  # noqa: F841 — kept for parity with simple_waker startup audit
            project_root=self.project_root, runtime_home=runtime_home
        )
        runtime_home.mkdir(parents=True, exist_ok=True)

        client = PersonaAgentClient(
            persona=persona,
            state=agent_state,
            save_state_fn=save_state,
            project_root=self.project_root,
            extra_env=extra_env,
            model=self.model,
            integration="orchestrator",
        )

        self._clients[persona] = client
        self._states[persona] = (state_file, state)
        return client


def run_invoke(
    *,
    persona: str,
    prompt: str,
    project_root: Path | None = None,
    new_session: bool = False,
    model: str | None = None,
    ignore_waker: bool = False,
    timeout: float | None = None,
    follow: bool = False,
    on_stream: Callable[[dict[str, Any]], None] | None = None,
) -> InvokeResult:
    """Synchronous wrapper for :meth:`HostOrchestrator.invoke`.

    Creates a fresh :class:`HostOrchestrator`, invokes the target persona,
    disconnects, and returns the result. ``timeout`` / ``follow`` / ``on_stream``
    are forwarded verbatim (see :meth:`HostOrchestrator.invoke`).
    """

    async def _run() -> InvokeResult:
        orchestrator = HostOrchestrator(
            project_root=project_root,
            model=model,
            ignore_waker=ignore_waker,
        )
        try:
            return await orchestrator.invoke(
                persona,
                prompt,
                new_session=new_session,
                timeout=timeout,
                follow=follow,
                on_stream=on_stream,
            )
        finally:
            await orchestrator.disconnect_all()

    return asyncio.run(_run())
