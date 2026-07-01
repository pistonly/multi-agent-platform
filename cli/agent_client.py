"""Persistent in-process Claude SDK client with session resume.

Used by map-runtime-waker (event-driven wake) and legacy bridges. The process
holds ONE ``ClaudeSDKClient`` for its lifetime; ``claude_session_id`` is
persisted in local state so the next restart resumes the same Claude session
(via ``ClaudeAgentOptions(resume=...)``).
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict

logger = logging.getLogger("map.agent_client")

DEFAULT_ALLOWED_TOOLS: list[str] = [
    "Skill",
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "TodoWrite",
]

_CREDENTIAL_ENV_KEYS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
)
_MODEL_ENV_KEYS: tuple[str, ...] = ("ANTHROPIC_MODEL", "CLAUDE_MODEL")

_EXPORT_RE = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

IntegrationMode = Literal["bridge", "waker"]


class WakeUpEvent(TypedDict, total=False):
    """Streamed wake-up event consumed by bridge / waker cycle logs."""

    type: str  # "text" | "result" | "tool"
    content: str
    session_id: str
    is_error: bool


class PersonaAgentLike(Protocol):
    """Minimal Claude SDK client surface used by PersonaAgentClient."""

    async def connect(self, prompt: str | None = None) -> None: ...
    async def disconnect(self) -> None: ...
    async def query(self, prompt: str) -> None: ...
    def receive_response(self) -> Any: ...


class PersonaAgentClient:
    """Per-persona persistent Claude SDK client with long-lived connection."""

    def __init__(
        self,
        *,
        persona: str,
        state: dict[str, Any],
        save_state_fn: Callable[[], None],
        project_root: Path,
        extra_env: dict[str, str] | None = None,
        model: str | None = None,
        allowed_tools: list[str] | None = None,
        integration: IntegrationMode = "bridge",
        _client_factory: Callable[[Any], PersonaAgentLike] | None = None,
    ) -> None:
        self.persona = persona
        self.state = state
        self._save_state_fn = save_state_fn
        self.project_root = project_root
        self.extra_env = dict(extra_env or {})
        self.model = model or self._resolve_model()
        self.allowed_tools = list(allowed_tools or DEFAULT_ALLOWED_TOOLS)
        self.integration = integration
        self._client_factory = _client_factory
        self._client: PersonaAgentLike | None = None
        self._connected = False

    async def connect(self) -> None:
        if self._connected:
            return
        resume_session_id = self.state.get("claude_session_id")
        options = self._build_options(resume=resume_session_id)
        client = self._make_client(options)
        await client.connect()
        self._client = client
        self._connected = True
        logger.info(
            "[%s] Claude SDK connected (resume=%s, integration=%s)",
            self.persona,
            resume_session_id or "<new>",
            self.integration,
        )

    async def wake_up(
        self,
        prompt: str,
        *,
        on_event: Callable[[WakeUpEvent], None] | None = None,
    ) -> str:
        if not self._connected or self._client is None:
            await self.connect()
        assert self._client is not None

        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

        await self._client.query(prompt)
        result: ResultMessage | None = None
        async for msg in self._client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        if on_event is not None:
                            on_event({"type": "text", "content": block.text})
            elif isinstance(msg, ResultMessage):
                result = msg
                if on_event is not None:
                    on_event(
                        {
                            "type": "result",
                            "session_id": msg.session_id,
                            "is_error": bool(getattr(msg, "is_error", False)),
                        }
                    )

        if result is not None and getattr(result, "session_id", None):
            new_sid = result.session_id
            if new_sid != self.state.get("claude_session_id"):
                self.state["claude_session_id"] = new_sid
                self._save_state_fn()
        if result is not None and getattr(result, "is_error", False):
            status = "error"
        elif result is None:
            status = "no_response"
        else:
            status = "ok"

        self.state["last_wakeup_at"] = datetime.now(UTC).isoformat()
        self.state["last_wakeup_status"] = status
        self._save_state_fn()
        return status

    async def disconnect(self) -> None:
        if self._client is None or not self._connected:
            self._connected = False
            return
        try:
            await self._client.disconnect()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[%s] Claude SDK disconnect failed: %s", self.persona, exc)
        finally:
            self._client = None
            self._connected = False

    def _make_client(self, options: Any) -> PersonaAgentLike:
        if self._client_factory is not None:
            return self._client_factory(options)
        from claude_agent_sdk import ClaudeSDKClient

        return ClaudeSDKClient(options=options)

    def _build_options(self, *, resume: str | None) -> Any:
        from claude_agent_sdk import ClaudeAgentOptions

        env = self._resolve_env()
        append_prompt = self._system_append_prompt()
        kwargs: dict[str, Any] = dict(
            cwd=str(self.project_root),
            setting_sources=["project"],
            system_prompt={"type": "preset", "preset": "claude_code", "append": append_prompt},
            allowed_tools=self.allowed_tools,
            permission_mode="acceptEdits",
            env=env,
        )
        if resume:
            kwargs["resume"] = resume
        if self.model:
            kwargs["model"] = self.model
        return ClaudeAgentOptions(**kwargs)

    def _system_append_prompt(self) -> str:
        if self.integration == "waker":
            return (
                f"You are the **{self.persona}** persona of the MAP (Multi-Agent Platform) "
                "project. When woken by map-runtime-waker you receive a single wake event. "
                f"Confirm identity with `map --persona {self.persona} persona whoami`, read "
                f"latest work with `map --persona {self.persona} todos`, then handle only the "
                "event in the wake prompt using the appropriate skill from `.cursor/skills/`. "
                "Gather missing context via the `map` CLI; do not wait for the waker to supply "
                "a full plan."
            )
        return (
            f"You are the **{self.persona}** persona of the MAP (Multi-Agent Platform) "
            "project. When woken up by the bridge, ALWAYS start by running "
            f"`map --persona {self.persona} todos` to inspect pending work, then invoke "
            "the appropriate skill from `.cursor/skills/` (topic-host, topic-participant, "
            "topic-reviewer, experiment-host, or map-project-collab) to act on each item. "
            "Do not wait for the bridge to feed you a complete plan; gather state "
            "yourself via the `map` CLI."
        )

    def _resolve_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        for key in _CREDENTIAL_ENV_KEYS:
            value = self._resolve_env_key(key)
            if value:
                env[key] = value
        env.update(self.extra_env)
        return env

    def _resolve_env_key(self, name: str) -> str | None:
        existing = os.environ.get(name, "").strip()
        if existing:
            return existing
        for path in self._candidate_rc_files():
            found = self._read_export(path, name)
            if found:
                return found
        return None

    def _resolve_model(self) -> str | None:
        for key in _MODEL_ENV_KEYS:
            value = self._resolve_env_key(key)
            if value:
                return value
        return None

    def _candidate_rc_files(self) -> list[Path]:
        home = Path.home()
        return [
            self.project_root / ".map" / ".claude-env",
            home / ".bashrc",
            home / ".profile",
            home / ".bash_profile",
        ]

    @staticmethod
    def _read_export(path: Path, name: str) -> str | None:
        if not path.is_file():
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        for line in text.splitlines():
            match = _EXPORT_RE.match(line)
            if match and match.group(1) == name:
                value = match.group(2).strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                elif value and value[0] not in {"'", '"'}:
                    value = re.sub(r"\s+#.*$", "", value).strip()
                if value:
                    return value
        return None


def make_wakeup_prompt(persona: str, todos: dict[str, Any]) -> str:
    """Build a brief bridge wake-up prompt from a todos snapshot."""
    lines = [
        f"You are the **{persona}** persona of MAP.",
        "",
        "You have pending work. Inspect it, then act:",
        "",
        f"  $ map --persona {persona} todos",
        "",
    ]
    pending_replies = todos.get("pending_replies") or []
    pending_topic_replies = todos.get("pending_topic_replies") or []
    pending_reviews = todos.get("pending_reviews") or []
    my_open_experiments = todos.get("my_open_experiments") or []
    my_open_topics = todos.get("my_open_topics") or []
    mentions = todos.get("mentions") or []

    if pending_replies:
        lines.append(f"- {len(pending_replies)} pending review reply(ies)")
    if pending_topic_replies:
        lines.append(f"- {len(pending_topic_replies)} pending topic reply(ies)")
    if pending_reviews:
        lines.append(f"- {len(pending_reviews)} pending review(s)")
    if my_open_experiments:
        lines.append(f"- {len(my_open_experiments)} open experiment(s)")
    if my_open_topics:
        lines.append(f"- {len(my_open_topics)} open topic(s)")
    if mentions:
        lines.append(f"- {len(mentions)} mention(s)")
    if not any(
        [
            pending_replies,
            pending_topic_replies,
            pending_reviews,
            my_open_experiments,
            my_open_topics,
            mentions,
        ]
    ):
        lines.append("(no pending items — short-circuit and report idle)")

    lines.extend(
        [
            "",
            "Use the appropriate skill from `.cursor/skills/` (topic-host, "
            "topic-participant, topic-reviewer, experiment-host, or "
            "map-project-collab). Report a brief status when done.",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "DEFAULT_ALLOWED_TOOLS",
    "IntegrationMode",
    "PersonaAgentClient",
    "PersonaAgentLike",
    "WakeUpEvent",
    "make_wakeup_prompt",
]
