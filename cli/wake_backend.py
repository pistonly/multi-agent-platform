"""Wake backend 与 todo bucket 元数据，供 simple-waker / runtime-waker 共用。

本模块抽离自 ``cli.runtime_waker``，目的是让 simple-waker 不再依赖
legacy runtime-waker 模块即可独立工作。``runtime_waker.py`` 通过
re-export 保持向后兼容，既有测试不需要修改。
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cli.agent_client import PersonaAgentClient
from cli.errors import WorkerError

# ---------------------------------------------------------------------------
# WakeResult — waker 调用 backend 后的统一返回值
# ---------------------------------------------------------------------------


@dataclass
class WakeResult:
    session_id: str | None = None
    response_text: str | None = None
    skipped: bool = False


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


def sync_runtime_skills(*, project_root: Path, runtime_home: Path) -> None:
    source_root = project_root / ".cursor" / "skills"
    if not source_root.is_dir():
        return
    target_root = runtime_home / ".claude" / "skills"
    target_root.mkdir(parents=True, exist_ok=True)
    source_names: set[str] = set()
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
    for existing in target_root.iterdir():
        if existing.is_dir() and existing.name not in source_names:
            shutil.rmtree(existing)


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
        state.pop("claude_session_id", None)
        state.pop("runtime_session_id", None)
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


# ---------------------------------------------------------------------------
# Todo bucket stable-id 派生 —— 从 cli.runtime_waker 迁入，供 simple-waker /
# 测试直接使用（runtime_waker 下线后不再 re-export）。
# ---------------------------------------------------------------------------


def _my_open_experiment_wake_stable_id(item: dict[str, Any]) -> str | None:
    """Derive waker fingerprint suffix for a my_open_experiments row.

    Includes phase + plan version + open unreasonable + log count so approve/start
    and each execution log produce distinct wakes (running with log_count=0 is visible).
    """
    exp_id = str(item.get("id") or "") or None
    if not exp_id:
        return None
    phase = str(item.get("phase") or "")
    plan_v = int(item.get("current_plan_version") or 0)
    open_u = int(item.get("open_unreasonable_count") or 0)
    log_n = int(item.get("log_count") or 0)
    return f"{exp_id}:{phase}:pv{plan_v}:ou{open_u}:lc{log_n}"


def _todo_item_stable_id(bucket: str, item: dict[str, Any]) -> str | None:
    if bucket == "my_open_experiments":
        return _my_open_experiment_wake_stable_id(item)
    if bucket == "mentions":
        return str(item.get("id") or "") or None
    if bucket == "pending_topic_replies":
        return str(item.get("comment_id") or "") or None
    if bucket == "pending_replies":
        return str(item.get("item_id") or item.get("id") or "") or None
    if bucket == "pending_round_acks":
        topic_id = item.get("topic_id")
        if not topic_id:
            return None
        summary_id = item.get("summary_comment_id") or "pending"
        return f"{topic_id}:{summary_id}"
    if bucket == "pending_advance_rounds":
        topic_id = item.get("topic_id")
        if not topic_id:
            return None
        pending_since = item.get("advance_round_pending_since") or item.get("updated_at") or ""
        return f"{topic_id}:{pending_since}"
    if bucket == "notification":
        return str(item.get("id") or "") or None
    return str(item.get("id") or "") or None
