"""交互会话桥接核心（实验 db97aeac I1）。

Claude Code Stop hook 等会话侧桥接的纯逻辑层：从 ``map work`` JSON 提取
工作指纹（fingerprint），并按「有限升级 + 提前沉默」语义决定是否提醒。
桥接层只读平台事实 + 去重/投递，不写 MAP、不做业务判断。

state 文件：``.map/interactive-bridge-state-<persona>.json``（实例分离，
与 simple-waker 的 ``simple-waker-state-<persona>.json`` 不共享、无锁）。
字段命名/语义沿用 waker 既有约定（fingerprint ≈ waker 的 wake_signature、
min_remind_seconds 冷却、last_seen_at 心跳供 waker 软信号降级读取）。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cli.bridge_state import load_bridge_state, save_bridge_state

BRIDGE_STATE_BASENAME = "interactive-bridge-state-{persona}.json"

DEFAULT_MIN_REMIND_SECONDS = 900.0
DEFAULT_MAX_REMIND_COUNT = 3

_OBLIGATION_TODO_BUCKETS = (
    "executor_assignments",
    "pending_reviews",
    "pending_result_reviews",
    "pending_replies",
    "pending_plan_revisions",
    "pending_topic_replies",
    "pending_round_acks",
    "pending_advance_rounds",
    "stale_open_topics",
    "mentions",
    "action_items",
)


def bridge_state_path(project_root: Path, persona: str) -> Path:
    return project_root / ".map" / BRIDGE_STATE_BASENAME.format(persona=persona)


def work_fingerprint(work: dict[str, Any]) -> str:
    """工作集指纹：相同待办集合 → 相同指纹（对齐 waker wake_signature 语义）。

    只取 obligation 面：topic_progress 里 priority=obligation 的 work item
    （idempotency_key 天然含内容锚点）、obligation 待办分区的条目数、
    未读通知 id。contextual 项不进指纹（不唤醒，只在有提醒时随摘要展示）。
    """
    parts: list[str] = []
    progress = work.get("topic_progress") or {}
    for item in progress.get("items") or []:
        if not isinstance(item, dict):
            continue
        for wi in item.get("work_items") or []:
            if isinstance(wi, dict) and wi.get("priority") == "obligation":
                parts.append(f"topic:{wi.get('idempotency_key') or wi.get('kind')}")
    todos = work.get("todos") or {}
    for bucket in _OBLIGATION_TODO_BUCKETS:
        entries = todos.get(bucket) or []
        parts.append(f"todo:{bucket}:{len(entries)}")
    notifications = work.get("notifications") or {}
    for entry in notifications.get("items") or []:
        if isinstance(entry, dict) and entry.get("id"):
            parts.append(f"notif:{entry['id']}")
    return hashlib.sha256("|".join(sorted(parts)).encode("utf-8")).hexdigest()[:16]


def has_obligation_work(work: dict[str, Any]) -> bool:
    """存在 obligation 级待办（topic work item / 待办分区 / 未读通知）。"""
    progress = work.get("topic_progress") or {}
    for item in progress.get("items") or []:
        if not isinstance(item, dict):
            continue
        for wi in item.get("work_items") or []:
            if isinstance(wi, dict) and wi.get("priority") == "obligation":
                return True
    todos = work.get("todos") or {}
    if any(len(todos.get(bucket) or []) > 0 for bucket in _OBLIGATION_TODO_BUCKETS):
        return True
    notifications = work.get("notifications") or {}
    return bool(notifications.get("unread_count"))


def evaluate_remind(
    state: dict[str, Any],
    *,
    fingerprint: str,
    now: datetime,
    min_remind_seconds: float = DEFAULT_MIN_REMIND_SECONDS,
    max_remind_count: int = DEFAULT_MAX_REMIND_COUNT,
) -> dict[str, Any]:
    """提醒判定（有限升级 + 提前沉默）。

    返回 dict：
    - ``remind`` (bool)：本次是否提醒；
    - ``block`` (bool)：是否 block 回合（仅某指纹的首次提醒）；
    - ``escalation`` (int)：第几次提醒（1 起），remind=False 时为 0；
    - ``reason`` (str)：new / repeat / cooldown / silenced，供日志与测试。
    """
    last_fingerprint = state.get("fingerprint")
    if last_fingerprint != fingerprint:
        return {"remind": True, "block": True, "escalation": 1, "reason": "new"}
    count = int(state.get("remind_count") or 0)
    if count >= max_remind_count:
        return {"remind": False, "block": False, "escalation": 0, "reason": "silenced"}
    last_at = _parse_dt(state.get("last_reminded_at"))
    if last_at is not None and (now - last_at).total_seconds() < min_remind_seconds:
        return {"remind": False, "block": False, "escalation": 0, "reason": "cooldown"}
    return {"remind": True, "block": False, "escalation": count + 1, "reason": "repeat"}


def record_trigger(
    state: dict[str, Any],
    *,
    now: datetime,
    runtime: str,
) -> dict[str, Any]:
    """每次 hook 触发都写 last_seen_at 心跳（waker 软信号降级的读取面）。"""
    state["last_seen_at"] = now.isoformat()
    state.setdefault("runtime", runtime)
    return state


def record_remind(
    state: dict[str, Any],
    *,
    fingerprint: str,
    now: datetime,
    decision: dict[str, Any],
) -> dict[str, Any]:
    """提醒发生后更新 fingerprint / 计数 / 时间戳（新指纹重置计数）。"""
    if state.get("fingerprint") != fingerprint:
        state["fingerprint"] = fingerprint
        state["remind_count"] = 1
        state["first_reminded_at"] = now.isoformat()
    else:
        state["remind_count"] = int(state.get("remind_count") or 0) + 1
    state["last_reminded_at"] = now.isoformat()
    state["last_escalation"] = decision.get("escalation", 0)
    return state


def load_state(path: Path | None) -> dict[str, Any]:
    return load_bridge_state(
        path, bridge_name="interactive-bridge", default_collections=(), validate_schema=False
    )


def save_state(path: Path | None, state: dict[str, Any]) -> None:
    save_bridge_state(path, state)


def _parse_dt(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
