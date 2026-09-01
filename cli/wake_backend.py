"""Wake backend 与 todo bucket 元数据，供 simple-waker / runtime-waker 共用。

本模块抽离自 ``cli.runtime_waker``，目的是让 simple-waker 不再依赖
legacy runtime-waker 模块即可独立工作。``runtime_waker.py`` 通过
re-export 保持向后兼容，既有测试不需要修改。
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from cli.agent_client import PersonaAgentClient
from cli.errors import WorkerError

WAKER_RUNTIMES: frozenset[str] = frozenset({"claude", "cursor"})
RUNTIME_SESSION_KEYS: tuple[str, ...] = (
    "claude_session_id",
    "runtime_session_id",
    "cursor_agent_id",
    "runtime_backend",
)

# ---------------------------------------------------------------------------
# WakeResult — waker 调用 backend 后的统一返回值
# ---------------------------------------------------------------------------


@dataclass
class WakeResult:
    session_id: str | None = None
    response_text: str | None = None
    skipped: bool = False


class WakeBackend(Protocol):
    """Minimal surface simple-waker uses to remind an Agent Runtime."""

    async def connect(self) -> None: ...

    async def wake_async(
        self,
        *,
        prompt: str,
        event_id: str | None = None,
        event_source: str = "polling",
        fingerprint: str | None = None,
    ) -> WakeResult: ...

    async def disconnect(self) -> None: ...

    async def reset_session(self) -> None: ...


def resolve_waker_runtime(cli_value: str | None = None) -> str:
    """Resolve ``--runtime`` / ``MAP_SIMPLE_RUNTIME`` to ``claude`` or ``cursor``.

    Precedence: explicit CLI value > ``MAP_SIMPLE_RUNTIME`` > ``claude``.
    """
    raw = (cli_value or "").strip().lower()
    if not raw:
        raw = (os.environ.get("MAP_SIMPLE_RUNTIME") or "claude").strip().lower()
    if raw not in WAKER_RUNTIMES:
        expected = ", ".join(sorted(WAKER_RUNTIMES))
        raise WorkerError(f"Unknown waker runtime {raw!r}; expected one of: {expected}")
    return raw


def clear_runtime_session_state(state: dict[str, Any]) -> None:
    """Drop persisted runtime ids so the next connect starts a fresh session."""
    for key in RUNTIME_SESSION_KEYS:
        state.pop(key, None)


def build_wake_backend(
    *,
    runtime: str,
    project_root: Path,
    persona: str,
    get_agent_state: Callable[[], dict[str, Any]],
    save_state_fn: Callable[[], None],
    model: str | None = None,
    runtime_home: Path | None = None,
) -> WakeBackend:
    """Construct the Agent Runtime backend for a simple-waker process."""
    if runtime == "cursor":
        from cli.cursor_wake_backend import CursorSdkWakeBackend

        return CursorSdkWakeBackend(
            project_root=project_root,
            persona=persona,
            get_agent_state=get_agent_state,
            save_state_fn=save_state_fn,
            model=model,
        )
    if runtime == "claude":
        return PersonaAgentWakeBackend(
            project_root=project_root,
            persona=persona,
            get_agent_state=get_agent_state,
            save_state_fn=save_state_fn,
            model=model,
            runtime_home=runtime_home,
        )
    expected = ", ".join(sorted(WAKER_RUNTIMES))
    raise WorkerError(f"Unknown waker runtime {runtime!r}; expected one of: {expected}")


# ---------------------------------------------------------------------------
# Todo bucket 元数据 — 镜像 web TodosPage 顺序 (GET /agents/me/todos)
# ---------------------------------------------------------------------------

TODO_WAKE_BUCKETS: tuple[str, ...] = (
    "mentions",
    "pending_topic_replies",
    "stale_open_topics",
    "action_items",
    "pending_plan_revisions",
    "pending_reviews",
    "pending_result_reviews",
    "pending_replies",
    "pending_round_acks",
    "pending_advance_rounds",
    "my_open_experiments",
    "my_open_topics",
)

TODO_BUCKET_UI_LABELS: dict[str, str] = {
    "mentions": "你有未处理的 @提及",
    "pending_topic_replies": "你有话题待回复",
    "stale_open_topics": "你有久未推进的开放话题",
    "action_items": "你有待跟进行动项",
    "pending_plan_revisions": "你有实验计划待修订",
    "pending_reviews": "你有实验待评审",
    "pending_result_reviews": "你有实验结果待审批",
    "pending_replies": "你有评审待回复",
    "pending_round_acks": "你有 Round Summary 待 ack",
    "pending_advance_rounds": "你有话题待推进轮次（ack 已齐）",
    "my_open_experiments": "你有进行中的实验需关注",
    "my_open_topics": "你有进行中的话题需关注",
    "notification": "你有未读通知",
}


# ---------------------------------------------------------------------------
# Skill chain 同步 — 把 .cursor/skills 拷贝到 runtime HOME
# ---------------------------------------------------------------------------


def sync_runtime_skills(
    *, project_root: Path, runtime_home: Path
) -> tuple[list[str], str | None]:
    """Mirror ``.cursor/skills/<skill>`` to ``<runtime_home>/.claude/skills/<skill>``.

    全量镜像（rmtree+copytree+孤儿清理）行为保持不变；返回
    ``(synced_skills, skipped_reason)``: ``synced_skills`` 是同步成功的
    skill 列表;``skipped_reason`` 仅在 ``synced_skills == []`` 时为
    ``source_missing``（源 .cursor/skills 不存在）;PermissionError 由
    调用方捕获并派生 ``permission_denied``。
    """
    source_root = project_root / ".cursor" / "skills"
    if not source_root.is_dir():
        return ([], "source_missing")
    target_root = runtime_home / ".claude" / "skills"
    target_root.mkdir(parents=True, exist_ok=True)
    source_names: set[str] = set()
    synced: list[str] = []
    for source in sorted(source_root.iterdir()):
        if not source.is_dir() or not (source / "SKILL.md").is_file():
            continue
        source_names.add(source.name)
        target = target_root / source.name
        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        shutil.copytree(source, target)
        synced.append(source.name)
    for existing in target_root.iterdir():
        if existing.is_dir() and existing.name not in source_names:
            shutil.rmtree(existing)
    return (synced, None)


# ---------------------------------------------------------------------------
# PersonaAgentWakeBackend — 长生命周期的 Claude backend
# ---------------------------------------------------------------------------


class PersonaAgentWakeBackend:
    """Long-lived Claude backend: one PersonaAgentClient per waker process."""

    def __init__(
        self,
        *,
        project_root: Path,
        persona: str,
        get_agent_state: Callable[[], dict[str, Any]],
        save_state_fn: Callable[[], None],
        model: str | None = None,
        runtime_home: Path | None = None,
        agent_client: PersonaAgentClient | None = None,
    ) -> None:
        self.project_root = project_root
        self.persona = persona
        self._get_agent_state = get_agent_state
        self._save_state_fn = save_state_fn
        self.model = model
        self.runtime_home = runtime_home
        self._agent_client = agent_client

    async def connect(self) -> None:
        if self._agent_client is None:
            if self.runtime_home is not None:
                sync_runtime_skills(project_root=self.project_root, runtime_home=self.runtime_home)
            extra_env: dict[str, str] = {"MAP_RUNTIME_WAKER_PERSONA": self.persona}
            if self.runtime_home is not None:
                extra_env["HOME"] = str(self.runtime_home)
            self._agent_client = PersonaAgentClient(
                persona=self.persona,
                state=self._get_agent_state(),
                save_state_fn=self._save_state_fn,
                project_root=self.project_root,
                extra_env=extra_env,
                model=self.model,
                integration="waker",
            )
        await self._agent_client.connect()
        state = self._get_agent_state()
        if state.get("runtime_backend") != "claude":
            state["runtime_backend"] = "claude"
            self._save_state_fn()

    async def wake_async(
        self,
        *,
        prompt: str,
        event_id: str | None = None,
        event_source: str = "polling",
        fingerprint: str | None = None,
    ) -> WakeResult:
        await self.connect()
        assert self._agent_client is not None
        status = await self._agent_client.wake_up(
            prompt,
            event_id=event_id,
            event_source=event_source,
            fingerprint=fingerprint,
        )
        state = self._get_agent_state()
        session_id = state.get("claude_session_id")
        state["runtime_backend"] = "claude"
        if session_id:
            state["runtime_session_id"] = session_id
        self._save_state_fn()
        if status == "error":
            raise WorkerError(f"Claude wake failed with status={status!r}")
        return WakeResult(session_id=session_id)

    async def disconnect(self) -> None:
        if self._agent_client is not None:
            await self._agent_client.disconnect()

    async def reset_session(self) -> None:
        """Disconnect so the next wake reconnects without resuming the prior session."""
        if self._agent_client is not None:
            await self._agent_client.disconnect()
            self._agent_client = None
        state = self._get_agent_state()
        clear_runtime_session_state(state)
        self._save_state_fn()

    def wake(
        self,
        *,
        persona: str,
        prompt: str,
        session_id: str | None,
    ) -> WakeResult:
        del persona, session_id
        return asyncio.run(self.wake_async(prompt=prompt))
