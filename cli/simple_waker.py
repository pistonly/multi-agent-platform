"""Thin MAP waker: poll unified work snapshot, remind agent when there is new work.

Waker logic stays minimal: the platform serves ``GET /agents/me/work``
(whoami + topic-progress + todos + wakeable notifications); agents use
``map work`` / ``map topic progress`` in Skills.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import signal
import uuid
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer

from cli.action_item_escalation import (
    ActionItemWakeDecision,
    scan_pending_action_items,
)
from cli.agent_client import apply_project_claude_env
from cli.bridge_state import load_bridge_state, save_bridge_state
from cli.cursor_wake_backend import apply_project_cursor_env
from cli.drift_detector import DriftDetector
from cli.errors import WorkerError
from cli.map_command_client import MapCommandClient
from cli.map_sdk_client import MapSdkClient
from cli.wake_backend import (
    TODO_BUCKET_UI_LABELS,
    TODO_WAKE_BUCKETS,
    WakeBackend,
    build_wake_backend,
    resolve_waker_runtime,
    sync_runtime_skills,
)
from cli.worker_cycle_log import log_cycle_summary

APP = typer.Typer(add_completion=False)

_skill_audit_logger = logging.getLogger("cli.simple_waker.skill_audit")
_audit_drift_logger = logging.getLogger("cli.simple_waker.verify_audit")


def _startup_sync_with_audit(project_root: Path, runtime_home: Path) -> None:
    """启动时同步 skills 并按 plan v0.x §A5 固定 JSON schema 留痕。

    skipped_reason 枚举: ``source_missing`` (源 .cursor/skills 不存在)
    / ``permission_denied`` (PermissionError 派生) / ``disabled`` (显式
    关闭: 通过环境变量 ``WAKER_SKILL_SYNC_DISABLED=1`` 跳过)。
    """
    if os.environ.get("WAKER_SKILL_SYNC_DISABLED") == "1":
        _skill_audit_logger.info(
            json.dumps(
                {
                    "event": "startup_sync",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "skills_count": 0,
                    "synced_skills": [],
                    "skipped_reason": "disabled",
                },
                ensure_ascii=False,
            )
        )
        return
    try:
        synced, skipped_reason = sync_runtime_skills(
            project_root=project_root, runtime_home=runtime_home
        )
    except PermissionError as exc:
        _skill_audit_logger.warning(
            json.dumps(
                {
                    "event": "startup_sync",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "skills_count": 0,
                    "synced_skills": [],
                    "skipped_reason": "permission_denied",
                    "error": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
            )
        )
        return
    _skill_audit_logger.info(
        json.dumps(
            {
                "event": "startup_sync",
                "ts": datetime.now(timezone.utc).isoformat(),
                "skills_count": len(synced),
                "synced_skills": synced,
                "skipped_reason": skipped_reason,
            },
            ensure_ascii=False,
        )
    )

# Passive inventory — not used alone to wake (topic activity uses topic-progress).
SIMPLE_WAKER_PASSIVE_BUCKETS: frozenset[str] = frozenset({"my_open_topics"})

_EXCERPT_IN_PROMPT = 280
RUNTIME_CONTRACT_VERSION = "simple-waker-runtime-contract-v3"
RUNTIME_CONTRACT_FILES: tuple[str, ...] = (
    ".cursor/skills/map-project-collab/SKILL.md",
    ".cursor/skills/topic-host/SKILL.md",
    ".cursor/skills/topic-participant/SKILL.md",
    ".cursor/skills/experiment-host/SKILL.md",
    ".cursor/skills/experiment-reviewer/SKILL.md",
)


def runtime_contract_hash(project_root: Path) -> str:
    """Hash the runtime-facing prompt/Skill contract that may affect agent behavior."""
    digest = hashlib.sha256()
    digest.update(RUNTIME_CONTRACT_VERSION.encode("utf-8"))
    for relative in RUNTIME_CONTRACT_FILES:
        path = project_root / relative
        digest.update(relative.encode("utf-8"))
        if path.is_file():
            digest.update(path.read_bytes())
        else:
            digest.update(b"<missing>")
    return digest.hexdigest()


@dataclass(frozen=True)
class TopicProgressEntry:
    topic_id: str
    topic_title: str
    discussion_round: str
    last_comment_author_name: str | None
    new_comment_count: int
    new_comments: tuple[dict[str, Any], ...]
    work_item_kinds: tuple[str, ...]
    # top-K 配额排序键:obligation 优先、义务越老越先(批量涌入时单次
    # wake 只推前 K 个,余量下轮自动到——防一次 remind 塞爆 session)。
    has_obligation: bool = False
    oldest_work_at: datetime | None = None


@dataclass(frozen=True)
class PendingBucket:
    kind: str
    count: int
    label: str
    samples: tuple[str, ...] = ()


@dataclass(frozen=True)
class WakeContext:
    topic_progress: tuple[TopicProgressEntry, ...] = ()
    todo_buckets: tuple[PendingBucket, ...] = ()
    notification_count: int = 0
    # race experiment (eca0f522) PR3: per-cycle dedup keeps
    # ``notification_count`` consistent with the DB's UNIQUE(recipient,
    # group_key) constraint, but the raw ``event_count`` from the
    # surviving (highest wake_version) row is preserved here so the
    # audit path can still report how many underlying events fed each
    # wakeable notification. Without this split, dedup would silently
    # compress the event_count in the aggregated stats.
    notification_event_count_sum: int = 0
    notification_dedup_dropped: int = 0
    # 通知身份键（group_key 或 id，排序后入 wake 签名）：签名去重需要区分
    # 「同样是 1 条通知，但还是那条僵尸」与「来了条新通知」。
    notification_keys: tuple[str, ...] = ()
    open_topic_count: int = 0
    open_topic_samples: tuple[dict[str, Any], ...] = ()
    drain_topics: bool = False
    # top-K 截断后被推迟、留待下轮 remind 的话题义务数(prompt 中注明,
    # 不计入本轮 total_items——本轮是否 wake 由截断后的余量决定)。
    topic_deferred_count: int = 0

    @property
    def topic_update_count(self) -> int:
        return len(self.topic_progress)

    @property
    def todo_item_count(self) -> int:
        return sum(bucket.count for bucket in self.todo_buckets)

    @property
    def total_items(self) -> int:
        return self.topic_update_count + self.todo_item_count + self.notification_count + self.open_topic_count

    @property
    def has_work(self) -> bool:
        return self.total_items > 0


@dataclass
class SimpleWakerConfig:
    persona: str = "host"
    project_root: Path = Path(".")
    map_cmd: str = "map"
    active_interval: float = 30.0
    idle_interval: float = 300.0
    once: bool = False
    max_cycles: int | None = None
    dry_run: bool = False
    state_file: Path | None = Path(".map/simple-waker-state.json")
    model: str | None = None
    runtime: str = "claude"
    runtime_home: Path | None = None
    min_remind_seconds: float = 30.0
    # 签名去重的兜底：工作集签名与上次唤醒一致时跳过 remind，但超过该
    # 时长（秒）始终未唤醒则强制提醒一次，防签名漏掉某种状态信号。
    max_silence_seconds: float = 1800.0
    drain_topics: bool = False
    # top-K 配额:单次 remind prompt 最多推送的话题义务数(obligation 优先、
    # 越老越先),余量下轮自动到。0/None = 不限制(旧行为)。CLI flag
    # --max-prompt-topics 或 env MAP_SIMPLE_MAX_PROMPT_TOPICS。
    max_prompt_topics: int = 3
    # f873c287 I1(g): CLI flag → env var → server settings. When set,
    # the waker exports ``MAP_STALE_OPEN_TOPIC_THRESHOLD_MINUTES`` so
    # subprocess ``map work`` invocations and any in-process server
    # pick up the override. The server's ``Settings`` already reads the
    # env var (W21 I1(c)); this flag is just the waker-side entrypoint.
    stale_threshold_minutes: int | None = None
    # 实验 waker-runtime-skill-hotcheck I4：每 N 个 poll cycle 跑一次
    # runtime skill 漂移检测；<=0 表示关闭（默认 30）。
    drift_check_interval_cycles: int = 30
    # 实验 d12c328c I1：waker busy 容忍（minutes；与 server 侧
    # Settings.expected_remind_runtime_minutes 同源）。CLI 视图层
    # ``cli/waker_status_view.py`` 从 state.json 读 ``expected_remind_runtime_seconds``
    # 推导 busy 容忍阈值（fallback chain：state.json > env
    # ``MAP_EXPECTED_REMIND_RUNTIME_MINUTES`` > 30min default）。env
    # override 在 __init__ 解析后存入实例属性。
    expected_remind_runtime_minutes: int | None = None


@dataclass
class SimpleWakerStats:
    cycles: int = 0
    polls_with_work: int = 0
    polls_idle: int = 0
    reminds_sent: int = 0
    remind_skips_busy: int = 0
    remind_skips_cooldown: int = 0
    # 签名去重命中次数（工作集与上次唤醒一致 → 跳过 remind）。
    remind_skips_unchanged: int = 0
    remind_errors: int = 0
    dry_run_actions: int = 0
    # v0.10：每次 remind 后写一条聚合 inbound_event（fingerprint=
    # ``simple-remind:{persona}:{cycle_ts}``）作为可观测性审计。
    # ``duplicate`` = 服务端 409（同一 cycle 已写过，理论上不会发生）；
    # ``skipped`` = client 没有 inbound_event_record 方法（测试 mock）。
    inbound_events_recorded: int = 0
    inbound_events_duplicate: int = 0
    inbound_events_skipped: int = 0
    # v0.10：action_item escalation（移植自 legacy runtime-waker）。
    # simple-waker 在 remind 前扫描 open + owner=persona 的 action_items：
    # - STALE → 调 ``action mark-stale`` 标记过期（waker 职责，非 Agent）
    # - WAKE → 调 ``action mark-wake-sent`` 推进 wake_count，再 remind
    # - SKIP → 跳过
    action_items_wake: int = 0
    action_items_stale: int = 0
    action_items_skip: int = 0
    action_items_errors: int = 0
    # 单次 cycle 因瞬时错误（API 5xx / 子进程失败 / 身份解析失败）而未能完成。
    # 长驻 waker 兜底跳过该 cycle 并在下一周期重试，此计数仅用于可观测性。
    cycle_errors: int = 0
    stalled_lock_notifications: int = 0

    def add(self, other: SimpleWakerStats) -> None:
        self.cycles += other.cycles
        self.polls_with_work += other.polls_with_work
        self.polls_idle += other.polls_idle
        self.reminds_sent += other.reminds_sent
        self.remind_skips_busy += other.remind_skips_busy
        self.remind_skips_cooldown += other.remind_skips_cooldown
        self.remind_skips_unchanged += other.remind_skips_unchanged
        self.remind_errors += other.remind_errors
        self.dry_run_actions += other.dry_run_actions
        self.inbound_events_recorded += other.inbound_events_recorded
        self.inbound_events_duplicate += other.inbound_events_duplicate
        self.inbound_events_skipped += other.inbound_events_skipped
        self.action_items_wake += other.action_items_wake
        self.action_items_stale += other.action_items_stale
        self.action_items_skip += other.action_items_skip
        self.action_items_errors += other.action_items_errors
        self.cycle_errors += other.cycle_errors
        self.stalled_lock_notifications += other.stalled_lock_notifications


def parse_topic_progress(data: dict[str, Any] | None) -> tuple[TopicProgressEntry, ...]:
    if not isinstance(data, dict):
        return ()
    items = data.get("items") or []
    if not isinstance(items, list):
        return ()
    entries: list[TopicProgressEntry] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        topic_id = raw.get("topic_id")
        if not topic_id:
            continue
        new_comments = raw.get("new_comments") or []
        if not isinstance(new_comments, list):
            new_comments = []
        work_items = raw.get("work_items") or []
        kinds: list[str] = []
        has_obligation = False
        work_times: list[datetime] = []
        if isinstance(work_items, list):
            for item in work_items:
                if not (isinstance(item, dict) and item.get("kind")):
                    continue
                kinds.append(str(item["kind"]))
                if item.get("priority") == "obligation":
                    has_obligation = True
                for time_field in ("stale_since", "created_at"):
                    parsed = _parse_datetime(item.get(time_field) if isinstance(item.get(time_field), str) else None)
                    if parsed is not None:
                        work_times.append(parsed)
        entries.append(
            TopicProgressEntry(
                topic_id=str(topic_id),
                topic_title=str(raw.get("topic_title") or ""),
                discussion_round=str(raw.get("discussion_round") or ""),
                last_comment_author_name=(
                    str(raw["last_comment_author_name"]) if raw.get("last_comment_author_name") else None
                ),
                new_comment_count=int(raw.get("new_comment_count") or len(new_comments)),
                new_comments=tuple(c for c in new_comments if isinstance(c, dict)),
                work_item_kinds=tuple(kinds),
                has_obligation=has_obligation,
                oldest_work_at=min(work_times) if work_times else None,
            )
        )
    return tuple(entries)


def summarize_actionable_todos(todos: dict[str, Any]) -> tuple[PendingBucket, ...]:
    buckets: list[PendingBucket] = []
    for kind in TODO_WAKE_BUCKETS:
        if kind in SIMPLE_WAKER_PASSIVE_BUCKETS:
            continue
        raw_items = todos.get(kind) or []
        if not isinstance(raw_items, list):
            continue
        items = [item for item in raw_items if isinstance(item, dict) and _todo_item_is_actionable(kind, item)]
        count = len(items)
        if count <= 0:
            continue
        buckets.append(
            PendingBucket(
                kind=kind,
                count=count,
                label=TODO_BUCKET_UI_LABELS.get(kind, kind),
                samples=_todo_bucket_samples(kind, items),
            )
        )
    return tuple(buckets)


def _todo_item_is_actionable(kind: str, item: dict[str, Any]) -> bool:
    if kind != "my_open_experiments":
        return True
    if item.get("archived_at"):
        return False
    # f873c287 I1(f): ``informational_only`` experiments are read-only
    # for the host (decision authority held by another persona). They
    # MUST NOT enter the waker obligation bucket even if the legacy
    # ``actions=[]`` filter happens to keep them in the partition.
    if item.get("informational_only"):
        return False
    actions = item.get("actions") or []
    return isinstance(actions, list) and any(str(action).strip() for action in actions)


def _todo_bucket_samples(kind: str, items: list[dict[str, Any]]) -> tuple[str, ...]:
    if kind != "my_open_experiments":
        return ()
    samples: list[str] = []
    for item in items[:3]:
        exp_id = str(item.get("id") or item.get("experiment_id") or "?")
        title = str(item.get("title") or item.get("experiment_title") or "experiment")
        phase = str(item.get("phase") or "?")
        actions = item.get("actions") or []
        action_text = ", ".join(str(action) for action in actions if str(action).strip()) or "?"
        samples.append(f"{title} (`{exp_id}`) · phase={phase} · actions={action_text}")
    if len(items) > len(samples):
        samples.append(f"… 另有 {len(items) - len(samples)} 个实验")
    return tuple(samples)


def build_wake_context(
    *,
    topic_progress_data: dict[str, Any] | None,
    todos: dict[str, Any],
    notifications: list[dict[str, Any]] | None = None,
    persona: str | None = None,
    drain_topics: bool = False,
    open_topics: list[dict[str, Any]] | None = None,
    max_prompt_topics: int | None = None,
) -> WakeContext:
    filtered_progress = _filter_topic_progress_for_persona(
        persona,
        topic_progress_data,
        todos,
    )
    deduped_notifications, event_count_sum, dropped = _dedupe_notifications_by_group_key(notifications)
    topic_progress = parse_topic_progress(filtered_progress)
    deferred = 0
    if max_prompt_topics is not None and max_prompt_topics > 0 and len(topic_progress) > max_prompt_topics:
        # obligation 优先,同层按义务时间老→新;None 时间戳垫底。
        _epoch = datetime.min.replace(tzinfo=timezone.utc)
        ordered = sorted(
            topic_progress,
            key=lambda e: (
                0 if e.has_obligation else 1,
                e.oldest_work_at or _epoch,
            ),
        )
        kept, dropped_entries = ordered[:max_prompt_topics], ordered[max_prompt_topics:]
        topic_progress = tuple(kept)
        deferred = len(dropped_entries)
    return WakeContext(
        topic_progress=topic_progress,
        todo_buckets=summarize_actionable_todos(todos),
        notification_count=len(deduped_notifications),
        notification_event_count_sum=event_count_sum,
        notification_dedup_dropped=dropped,
        notification_keys=tuple(
            sorted(
                str(n.get("group_key") or n.get("id") or "")
                for n in deduped_notifications
                if isinstance(n, dict)
            )
        ),
        open_topic_count=len(open_topics or []) if drain_topics and persona == "host" else 0,
        open_topic_samples=tuple((open_topics or [])[:10]) if drain_topics and persona == "host" else (),
        drain_topics=drain_topics,
        topic_deferred_count=deferred,
    )


def _dedupe_notifications_by_group_key(
    notifications: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], int, int]:
    """Per-cycle dedup of wakeable notifications by ``group_key``.

    race experiment (eca0f522) PR3: PR2's DB-layer
    ``UNIQUE(recipient_agent_id, group_key)`` already prevents duplicate
    rows in steady state, but the simple-waker must not depend on that
    invariant — the work snapshot can still surface multiple rows during
    a brief window if a wakeable merge races with an unrelated read
    cursor refresh, or if a future migration relaxes the constraint.
    We dedupe defensively: for each ``group_key``, keep the row with the
    highest ``wake_version`` (the most recent merge), summing each
    surviving row's ``event_count`` so the audit path sees the raw
    underlying event volume (otherwise dedup would silently compress
    it). Rows with ``group_key=None`` are treated as unique entries
    (UNIQUE constraints ignore NULLs by design).
    """
    if not notifications:
        return [], 0, 0
    survivors_by_key: dict[str, dict[str, Any]] = {}
    raw_event_count = 0
    dropped = 0
    for raw in notifications:
        if not isinstance(raw, dict):
            continue
        group_key = raw.get("group_key")
        event_count = int(raw.get("event_count") or 1)
        if group_key is None:
            survivors_by_key[f"__none_:{id(raw)}"] = raw
            raw_event_count += event_count
            continue
        existing = survivors_by_key.get(group_key)
        if existing is None:
            survivors_by_key[group_key] = raw
            raw_event_count += event_count
            continue
        existing_wake = int(existing.get("wake_version") or 0)
        new_wake = int(raw.get("wake_version") or 0)
        existing_event_count = int(existing.get("event_count") or 1)
        if new_wake > existing_wake:
            # Replace: subtract the dropped row's event_count from the
            # raw total so we don't double-count.
            raw_event_count += event_count - existing_event_count
            survivors_by_key[group_key] = raw
            dropped += 1
        else:
            raw_event_count += event_count
            dropped += 1
    return list(survivors_by_key.values()), raw_event_count, dropped


def _filter_topic_progress_for_persona(
    persona: str | None,
    topic_progress_data: dict[str, Any] | None,
    todos: dict[str, Any],
) -> dict[str, Any] | None:
    """Reviewer with experiment review todos: suppress contextual-only topic progress."""
    if persona != "reviewer":
        return topic_progress_data
    if not (todos.get("pending_reviews") or todos.get("pending_result_reviews")):
        return topic_progress_data
    if not isinstance(topic_progress_data, dict):
        return topic_progress_data
    items = topic_progress_data.get("items") or []
    if not isinstance(items, list):
        return topic_progress_data
    filtered: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        work_items = raw.get("work_items") or []
        if not isinstance(work_items, list) or not work_items:
            filtered.append(raw)
            continue
        if any(isinstance(wi, dict) and wi.get("priority") == "obligation" for wi in work_items):
            filtered.append(raw)
    return {**topic_progress_data, "items": filtered, "total": len(filtered)}


def wake_signature(context: WakeContext) -> str:
    """唤醒内容签名：相同工作集 → 相同签名。

    高频轮询的安全网：签名不变 → 跳过 remind（冷却期之外也不再按固定
    节奏空醒）；签名变化（新话题 / 对方发言改变 last_comment_author /
    新通知键 / 待办计数变化）才唤醒 agent。配合 ``max_silence_seconds``
    兜底，防签名漏掉某种信号导致永久睡死。
    """
    parts: list[str] = []
    for entry in sorted(context.topic_progress, key=lambda e: e.topic_id):
        kinds = ",".join(sorted(entry.work_item_kinds))
        parts.append(
            f"topic:{entry.topic_id}:{entry.discussion_round}:{kinds}"
            f":{entry.last_comment_author_name or ''}:{entry.new_comment_count}"
        )
    for bucket in sorted(context.todo_buckets, key=lambda b: b.kind):
        parts.append(f"todo:{bucket.kind}:{bucket.count}")
    parts.extend(f"notif:{key}" for key in context.notification_keys)
    if context.open_topic_count:
        samples = ",".join(
            sorted(str(s.get("slug") or s.get("id") or "") for s in context.open_topic_samples)
        )
        parts.append(f"open:{context.open_topic_count}:{samples}")
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def should_send_remind(
    context: WakeContext,
    *,
    now: datetime,
    last_remind_at: datetime | None,
    inflight: bool,
    min_remind_seconds: float,
    signature: str | None = None,
    last_reminded_signature: str | None = None,
    max_silence_seconds: float = 1800.0,
) -> tuple[bool, str | None]:
    if not context.has_work:
        return False, "idle"
    if inflight:
        return False, "busy"
    if last_remind_at is not None:
        elapsed = (now - last_remind_at).total_seconds()
        if elapsed < min_remind_seconds:
            return False, "cooldown"
        # 内容级去重：工作集与上次唤醒时完全一致 → 不重复唤醒（僵尸
        # 通知、对方未动作的等待期都命中此项）；超过 max_silence_seconds
        # 仍未唤醒则兜底放行一次，防签名漏信号。
        if (
            signature is not None
            and signature == last_reminded_signature
            and elapsed < max_silence_seconds
        ):
            return False, "unchanged"
    return True, None


def _clip(text: str, limit: int = _EXCERPT_IN_PROMPT) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def build_remind_prompt(persona: str, context: WakeContext) -> str:
    command = f"map --persona {persona}"
    lines = [
        f"MAP 协作提醒 · {persona}",
        "",
        "平台检测到待处理 work items / todos actions。请先读 map-project-collab 的 references/wake.md（最小唤醒协议：四步 + kind→清理分发表），再读 persona Skill，然后：",
        f"1. `{command} persona whoami`",
        f"2. `{command} work` 或 `{command} topic progress` — topic work items 统一视图（obligation + contextual；与 todos 话题分区同源）",
        f"3. `{command} todos` — 实验/评审/mention 等待办分区",
        "4. 按 work_items.kind 或 todos.actions 逐项处理（obligation 优先）；topic 用 topic-host，pending_reviews 用 experiment-reviewer，my_open_experiments 用 experiment-host",
        "",
    ]

    if context.drain_topics and persona == "host":
        lines.extend(
            [
                "## Drain topics 模式",
                f"- 当前仍有 {context.open_topic_count} 个 open topic。",
                "- 请按 topic-host Skill 主持这些话题；host 应主动推动话题进展、积极解决问题。",
                "- 先逐个 topic show 复盘上下文，再选择下一步：comment / Round Summary / advance-round / resolve。",
                "- 更细的邀请、收敛、行动项和实验边界判断以 topic-host Skill 为准。",
            ]
        )
        for raw in context.open_topic_samples:
            topic_id = raw.get("id") or raw.get("topic_id")
            title = raw.get("title") or raw.get("topic_title") or ""
            round_name = raw.get("discussion_round") or ""
            comments = raw.get("comment_count")
            lines.append(f"- {title} (`{topic_id}`) · {round_name} · comments={comments}")
        if context.open_topic_count > len(context.open_topic_samples):
            lines.append(f"- … 另有 {context.open_topic_count - len(context.open_topic_samples)} 个 open topic")
        lines.append("")

    if context.topic_progress:
        lines.append("## 话题 work items（topic-progress）")
        for entry in context.topic_progress:
            kinds = ", ".join(entry.work_item_kinds) if entry.work_item_kinds else "?"
            who = entry.last_comment_author_name or "他人"
            lines.append(
                f"- **{entry.topic_title}** (`{entry.topic_id}`) "
                f"· {entry.discussion_round} · kinds: {kinds} · 末评 {who}"
            )
            if entry.new_comment_count > 0:
                lines.append(f"  - unread 摘要 +{entry.new_comment_count} 条：")
            for comment in entry.new_comments[:3]:
                author = comment.get("author_name") or comment.get("author_agent_id") or "?"
                excerpt = comment.get("excerpt") or comment.get("body") or ""
                lines.append(f"  - @{author}: {_clip(str(excerpt))}")
            if len(entry.new_comments) > 3:
                lines.append(f"  - … 另有 {len(entry.new_comments) - 3} 条，请 `topic show --id {entry.topic_id}`")
        if context.topic_deferred_count > 0:
            lines.append(
                f"- 另有 {context.topic_deferred_count} 个话题义务本轮未列出（批量涌入时按 obligation 优先、"
                "越老越先排队）——**只处理上面列出的项即可**，排队项下轮提醒自动到达，不要主动去 `map work` 清全量。"
            )
        lines.append("")

    if context.todo_buckets or context.notification_count:
        lines.append("## 其他待办")
        for bucket in context.todo_buckets:
            lines.append(f"- {bucket.kind}: {bucket.count}（{bucket.label}）")
            for sample in bucket.samples:
                lines.append(f"  - {sample}")
        if context.notification_count:
            lines.append(f"- notification: {context.notification_count}（{TODO_BUCKET_UI_LABELS['notification']}）")
        lines.append("")

    lines.append(f"详情：`{command} work` · `{command} topic progress` · `{command} todos`")
    return "\n".join(lines) + "\n"


def next_sleep_seconds(context: WakeContext, config: SimpleWakerConfig) -> float:
    return config.active_interval if context.has_work else config.idle_interval


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _use_subprocess_waker_client(flag: bool) -> bool:
    if flag:
        return True
    raw = os.environ.get("MAP_WAKER_SUBPROCESS", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def build_waker_client(
    *,
    persona: str,
    project_root: Path | None,
    map_cmd: str,
    dry_run: bool,
    subprocess_client: bool = False,
) -> MapCommandClient | MapSdkClient:
    """T24：默认 in-process SDK；``--subprocess-client`` / env 回退 ``map`` 子进程。"""
    if _use_subprocess_waker_client(subprocess_client):
        return MapCommandClient(
            persona=persona,
            project_root=project_root,
            map_cmd=map_cmd,
            dry_run=dry_run,
        )
    return MapSdkClient(persona=persona, project_root=project_root, dry_run=dry_run)


# Backward-compatible aliases for tests
PendingWorkSummary = WakeContext
summarize_pending_work = lambda todos, notifications=None: build_wake_context(  # noqa: E731
    topic_progress_data={"items": []},
    todos=todos,
    notifications=notifications,
    persona=None,
)


class SimpleWaker:
    def __init__(
        self,
        *,
        client: MapCommandClient | MapSdkClient,
        config: SimpleWakerConfig | None = None,
        backend: WakeBackend | None = None,
    ) -> None:
        self.client = client
        self.config = config or SimpleWakerConfig()
        # 实验 d12c328c I1：从 env 解析 expected_remind_runtime_minutes（CLI flag
        # 暂未暴露，与 server 侧 Settings 同源；env override 即可）。
        if self.config.expected_remind_runtime_minutes is None:
            env_val = os.environ.get("MAP_EXPECTED_REMIND_RUNTIME_MINUTES")
            if env_val:
                try:
                    self.config.expected_remind_runtime_minutes = int(env_val)
                except ValueError:
                    warnings.warn(
                        f"MAP_EXPECTED_REMIND_RUNTIME_MINUTES={env_val!r} is not a "
                        "valid integer; falling back to 30min default",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                    self.config.expected_remind_runtime_minutes = 30
            else:
                self.config.expected_remind_runtime_minutes = 30
        self.state = load_bridge_state(
            self.config.state_file,
            bridge_name="simple-waker",
            default_collections=("personas",),
        )
        # 实验 d12c328c I1：每个 persona state 镜像 expected_remind_runtime_seconds
        # （与 server 侧 Settings.expected_remind_runtime_minutes 同源）；CLI 视图层
        # 读此字段推导 busy 容忍（fallback chain：state.json > env > 30min default）。
        # setdefault 保证旧 state 不被覆盖（首次启动后写一次，后续保留）。
        persona_state = self._persona_state(self.config.persona)
        persona_state.setdefault(
            "expected_remind_runtime_seconds",
            int(self.config.expected_remind_runtime_minutes or 30) * 60,
        )
        self._state_dirty = True
        self._save_state_if_needed(force=True)
        self._state_dirty = False
        self._inflight = False
        # T03：本周期解析出的 persona 身份（work 快照的 agent 字段）。
        # 缓存后 action_item escalation 等下游消费点不再重复调 whoami 子进程。
        self._me: dict[str, Any] | None = None
        # T39：连续失败计数（指数退避）与优雅退出标志（SIGTERM/SIGINT）。
        self._consecutive_errors = 0
        self._stop_requested = False
        self._stop_event: asyncio.Event | None = None
        self.backend: WakeBackend = backend or build_wake_backend(
            runtime=self.config.runtime,
            project_root=self.config.project_root,
            persona=self.config.persona,
            get_agent_state=lambda: self._persona_state(self.config.persona),
            save_state_fn=lambda: self._save_state_if_needed(force=True),
            model=self.config.model,
            runtime_home=self.config.runtime_home,
        )
        self._runtime_contract_hash = runtime_contract_hash(self.config.project_root)
        # 实验 waker-runtime-skill-hotcheck I4：构造 DriftDetector。
        # source_root = .cursor/skills；dest_root = <runtime_home>/.claude/skills。
        # 若 source_root 不存在（极端场景），detector 退化为空 scan，resync 由
        # detector.resync 走 source_missing 路径——不抛异常。
        self._drift_detector = self._build_drift_detector()
        # 实验 b3ec2e4d I2：启动时回收上次 crash 残留的 busy 标记。
        # state 文件若含 stale busy_started_at + 已死 PID → 清零本地 + server。
        self._check_busy_crash_recovery()

    def run_forever(self) -> SimpleWakerStats:
        return asyncio.run(self._run_forever_async())

    async def _run_forever_async(self) -> SimpleWakerStats:
        if not self.config.dry_run:
            await self._reset_runtime_session_if_contract_changed()
            await self._reset_runtime_session_if_backend_changed()
        if not self.config.dry_run:
            await self.backend.connect()
        # T39：优雅退出——SIGTERM/SIGINT 置 stop 标志并唤醒 sleep，当前
        # cycle 结束后走 finally 的 disconnect（原先 KeyboardInterrupt 会
        # 直接炸出 asyncio.run，backend.disconnect() 不保证执行）。
        stop_event = asyncio.Event()
        self._stop_event = stop_event
        loop = asyncio.get_running_loop()
        registered_signals: list[int] = []
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._request_stop)
                registered_signals.append(sig)
            except (NotImplementedError, RuntimeError):
                # Windows / 非主线程：无 add_signal_handler。SIGINT 仍有
                # KeyboardInterrupt 默认路径，asyncio.run 的 finally 兜底。
                pass
        total = SimpleWakerStats()
        try:
            while True:
                try:
                    stats, sleep_for = await self._run_once_async()
                    self._consecutive_errors = 0
                except WorkerError as exc:
                    # 瞬时错误兜底：API 5xx / 子进程失败 / 身份解析失败等。
                    # 长驻 waker 不能因单次 cycle 失败退出——记错到 state，
                    # 按指数退避（T39）后下一 cycle 重试。持续失败会在
                    # state 累积 last_cycle_error 供运维观测。
                    typer.echo(f"[simple-waker:cycle-error] {exc}", err=True)
                    persona_state = self._persona_state(self.config.persona)
                    persona_state["last_cycle_error"] = str(exc)
                    persona_state["last_cycle_error_at"] = datetime.now(timezone.utc).isoformat()
                    self._state_dirty = True
                    self._save_state_if_needed(force=True)
                    self._consecutive_errors += 1
                    stats = SimpleWakerStats(cycles=1, cycle_errors=1)
                    sleep_for = self._backoff_interval()
                total.add(stats)
                # 实验 waker-runtime-skill-hotcheck I4：每 N 个 poll cycle 跑
                # 一次漂移检测；用 total.cycles 计数保证无论正常/异常路径都
                # 计数。失败已在 _run_drift_check 内捕获，不阻塞主流程。
                self._run_drift_check(cycle_index=total.cycles)
                # 实验 e6d23886 (T7-a) I3：verify-audit 检测同样按 N cycle 节流，
                # 失败/异常在 _run_verify_audit_check 内部捕获，不阻断 waker。
                self._run_verify_audit_check(cycle_index=total.cycles)
                log_cycle_summary(
                    "simple-waker",
                    total,
                    fields=[
                        "cycles",
                        "polls_with_work",
                        "polls_idle",
                        "reminds_sent",
                        "remind_skips_busy",
                        "remind_skips_cooldown",
                        "remind_skips_unchanged",
                        "remind_errors",
                        "dry_run_actions",
                        "inbound_events_recorded",
                        "inbound_events_duplicate",
                        "inbound_events_skipped",
                        "action_items_wake",
                        "action_items_stale",
                        "action_items_skip",
                        "action_items_errors",
                        "cycle_errors",
                    ],
                )
                if self.config.once:
                    break
                if self.config.max_cycles is not None and total.cycles >= self.config.max_cycles:
                    break
                if self._stop_requested:
                    # T39：信号已到——当前 cycle 已完整收尾，直接退出走
                    # finally 的 disconnect，不再进入下一个退避/轮询间隔。
                    typer.echo("[simple-waker] stop requested; exiting gracefully", err=True)
                    break
                # T39：sleep 可被 stop 信号提前唤醒（wait_for + Event），
                # 避免收到 SIGTERM 后还要空等最长 30min 的退避间隔。
                # asyncio.wait_for 超时在 Py3.10 抛 ``asyncio.TimeoutError``
                # （3.11 才与内置 TimeoutError 合并），故用 asyncio 版本捕获。
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=sleep_for)
            return total
        finally:
            for sig in registered_signals:
                with contextlib.suppress(Exception):
                    loop.remove_signal_handler(sig)
            self._stop_event = None
            if not self.config.dry_run:
                await self.backend.disconnect()
            close_fn = getattr(self.client, "close", None)
            if callable(close_fn):
                with contextlib.suppress(Exception):
                    close_fn()

    def _request_stop(self) -> None:
        """Signal handler body: set the stop flag and wake the sleep."""
        if self._stop_requested:
            return
        self._stop_requested = True
        if self._stop_event is not None:
            self._stop_event.set()

    def _backoff_interval(self) -> float:
        """T39：按连续失败次数指数退避（idle × 2^n，cap 30min）。"""
        backoff_cap = 1800.0
        factor = 2 ** min(self._consecutive_errors, 8)
        return min(self.config.idle_interval * factor, backoff_cap)

    def run_once(self) -> SimpleWakerStats:
        return asyncio.run(self._run_once_async())[0]

    async def _run_once_async(self) -> tuple[SimpleWakerStats, float]:
        stats = SimpleWakerStats(cycles=1)
        self._scan_stalled_experiment_locks(stats)
        # 实验 waker-status-view I2: 每个 cycle 自写 waker 状态指标 + pid 变化归档
        # 必须放在 _scan_stalled_experiment_locks 之后、所有 return 之前，确保
        # 每个 cycle（无论 remind / dry-run / no-remind 路径）都累加计数。
        self._accumulate_cycle_stats(stats)
        work = self.client.work() or {}
        # T03：work 快照本身含完整 agent 身份（AgentWorkRead.agent），且认证
        # 失败时 work 子进程同样非零退出——身份直接从快照取，省掉每周期一次
        # 独立的 whoami 子进程（完整 Python + Typer 冷启动）。
        self._ensure_identity(work)
        topic_progress_data = work.get("topic_progress") or {}
        todos = work.get("todos") or {}
        notifications_payload = work.get("notifications") or {}
        notifications = (
            list(notifications_payload.get("items") or []) if isinstance(notifications_payload, dict) else []
        )
        open_topics: list[dict[str, Any]] = []
        if self.config.drain_topics and self.config.persona == "host":
            open_topics = self.client.topic_list_open()
        context = build_wake_context(
            topic_progress_data=topic_progress_data,
            todos=todos,
            notifications=notifications,
            persona=self.config.persona,
            drain_topics=self.config.drain_topics,
            open_topics=open_topics,
            max_prompt_topics=self.config.max_prompt_topics,
        )
        if context.has_work:
            stats.polls_with_work = 1
        else:
            stats.polls_idle = 1

        persona_state = self._persona_state(self.config.persona)
        last_remind_at = _parse_datetime(persona_state.get("last_remind_at"))
        now = datetime.now(timezone.utc)
        signature = wake_signature(context)
        should_remind, skip_reason = should_send_remind(
            context,
            now=now,
            last_remind_at=last_remind_at,
            inflight=self._inflight,
            min_remind_seconds=self.config.min_remind_seconds,
            signature=signature,
            last_reminded_signature=persona_state.get("last_wake_signature"),
            max_silence_seconds=self.config.max_silence_seconds,
        )
        if not should_remind:
            if skip_reason == "busy":
                stats.remind_skips_busy = 1
            elif skip_reason == "cooldown":
                stats.remind_skips_cooldown = 1
            elif skip_reason == "unchanged":
                stats.remind_skips_unchanged = 1
            self._save_state_if_needed()
            return stats, next_sleep_seconds(context, self.config)

        prompt = build_remind_prompt(self.config.persona, context)
        if self.config.dry_run:
            typer.echo(
                f"[dry-run] would remind persona={self.config.persona} "
                f"topics={context.topic_update_count} todos={context.todo_item_count}"
            )
            typer.echo(prompt.rstrip())
            stats.dry_run_actions = 1
            self._save_state_if_needed()
            return stats, next_sleep_seconds(context, self.config)

        # v0.10：action_item escalation（移植自 legacy runtime-waker）。
        # 在 remind 前扫描 open + owner=persona 的 action_items，推进
        # wake_count（WAKE）或标记过期（STALE）。失败不阻塞 remind。
        self._apply_action_item_escalation(stats, todos=todos, now=now)

        self._inflight = True
        # 实验 b3ec2e4d I2：进入 runtime 调用（remind → claude 子进程）
        # 前 touch busy 心跳，会话结束清零。失败兜底不阻塞主流程（A8）。
        self._touch_busy(stats, now=now)
        try:
            await self.backend.wake_async(prompt=prompt, event_source="simple-waker")
            persona_state["last_remind_at"] = now.isoformat()
            persona_state["last_remind_work_count"] = context.total_items
            persona_state["last_remind_topic_count"] = context.topic_update_count
            persona_state["last_wake_signature"] = signature
            self._state_dirty = True
            stats.reminds_sent = 1
            # v0.10：写一条聚合 inbound_event 作为可观测性审计。
            # fingerprint 含 cycle 时间戳，确保每个 remind cycle 唯一；
            # 同一 cycle 内 min_remind_seconds 已防重，409 仅作并发兜底。
            self._record_remind_inbound_event(stats, now=now, work_count=context.total_items)
        except WorkerError as exc:
            stats.remind_errors = 1
            persona_state["last_remind_error"] = str(exc)
            persona_state["last_remind_error_at"] = now.isoformat()
            self._state_dirty = True
            typer.echo(f"[simple-waker:error] {exc}", err=True)
        finally:
            # I2：busy 心跳清零（与 _touch_busy 配对），server 列
            # agents.last_busy_since → NULL；PID 校验防止 crash 漏清。
            self._clear_busy(stats)
            self._inflight = False
            self._save_state_if_needed(force=True)
        return stats, next_sleep_seconds(context, self.config)

    def _scan_stalled_experiment_locks(self, stats: SimpleWakerStats) -> None:
        """Ask the platform to materialize stalled-lock notifications before polling work."""
        if self.config.persona != "host":
            return
        scan_fn = getattr(self.client, "experiment_scan_stalled_locks", None)
        if scan_fn is None or self.config.dry_run:
            return
        try:
            result = scan_fn() or {}
        except WorkerError as exc:
            stats.cycle_errors += 1
            typer.echo(f"[simple-waker:stalled-lock-scan] {exc}", err=True)
            return
        if isinstance(result, dict):
            stats.stalled_lock_notifications += int(result.get("emitted_count") or 0)

    def _build_drift_detector(self) -> DriftDetector | None:
        """构造 DriftDetector；runtime_home 缺失或源 skills 不存在时返回 None。

        source_root = ``<project_root>/.cursor/skills``；
        dest_root   = ``<runtime_home>/.claude/skills``。
        """
        runtime_home = self.config.runtime_home
        if runtime_home is None:
            return None
        source_root = self.config.project_root / ".cursor" / "skills"
        if not source_root.is_dir():
            return None
        return DriftDetector(
            source_root=source_root,
            dest_root=runtime_home / ".claude" / "skills",
        )

    def _run_drift_check(self, *, cycle_index: int) -> None:
        """每 ``drift_check_interval_cycles`` 周期跑一次漂移检测 + 立即重同步。

        - 漂移为空 → 写 ``drift_no_change`` 审计行（无 alert）
        - 漂移非空且 resync 成功 → ``drift_resync`` 审计行（alert=True 仅在失败时）
        - resync 失败 → ``drift_resync_failed`` 审计行（alert=True），但不让 waker 崩
        """
        interval = self.config.drift_check_interval_cycles
        if interval <= 0 or self._drift_detector is None:
            return
        if cycle_index % interval != 0:
            return
        try:
            entries = self._drift_detector.check_drift()
        except Exception as exc:
            _skill_audit_logger.warning(
                json.dumps(
                    {
                        "event": "drift_check_failed",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "cycle": cycle_index,
                        "alert": True,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    ensure_ascii=False,
                )
            )
            return
        if not entries:
            _skill_audit_logger.debug(
                json.dumps(
                    {
                        "event": "drift_no_change",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "cycle": cycle_index,
                    },
                    ensure_ascii=False,
                )
            )
            return
        result = self._drift_detector.resync(entries)
        payload = {
            "event": "drift_resync" if result.ok else "drift_resync_failed",
            "ts": datetime.now(timezone.utc).isoformat(),
            "cycle": cycle_index,
            "drift_skills": sorted({e.skill_relpath.split("/", 1)[0] for e in entries}),
            "resynced_skills": result.resynced_skills,
            "duration_ms": result.duration_ms,
            "throttled": result.skipped_due_to_throttle,
            "alert": not result.ok,
        }
        if not result.ok:
            payload["error"] = result.error
        log_fn = _skill_audit_logger.warning if not result.ok else _skill_audit_logger.info
        log_fn(json.dumps(payload, ensure_ascii=False))

    def _run_verify_audit_check(self, *, cycle_index: int) -> None:
        """每 ``drift_check_interval_cycles`` 周期跑一次 verify-audit 扫描。

        实验 e6d23886 (T7-a) I3：复用 T4 drift hotcheck 的 30-cycle 节流节奏，
        但只扫描 + WARN 上报，不写 audit.jsonl (防递归绕过)。失败/异常被内部
        捕获，不阻断 waker 主流程。

        - 干净 → DEBUG ``audit_drift_clean``
        - 有漂移 → WARNING ``audit_drift_detected`` (alert=True)
        - 扫描异常 → WARNING ``audit_drift_check_failed`` (alert=True)
        """
        interval = self.config.drift_check_interval_cycles
        if interval <= 0:
            return
        if cycle_index % interval != 0:
            return
        try:
            from cli.verify_audit import scan_plane_audit
            detector = scan_plane_audit(self.config.project_root)
        except Exception as exc:
            _audit_drift_logger.warning(
                json.dumps(
                    {
                        "event": "audit_drift_check_failed",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "cycle": cycle_index,
                        "alert": True,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    ensure_ascii=False,
                )
            )
            return
        if detector.count == 0:
            _audit_drift_logger.debug(
                json.dumps(
                    {
                        "event": "audit_drift_clean",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "cycle": cycle_index,
                    },
                    ensure_ascii=False,
                )
            )
            return
        _audit_drift_logger.warning(
            json.dumps(
                {
                    "event": "audit_drift_detected",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "cycle": cycle_index,
                    "drift_count": detector.count,
                    "drift_ids": [d.drift_id for d in detector.drifts],
                    "kinds": sorted({d.kind for d in detector.drifts}),
                    "alert": True,
                },
                ensure_ascii=False,
            )
        )

    def _record_remind_inbound_event(
        self,
        stats: SimpleWakerStats,
        *,
        now: datetime,
        work_count: int,
    ) -> None:
        """写一条聚合 inbound_event 审计行（fingerprint=simple-remind:{persona}:{ts}）。

        服务端 ``POST /agents/me/inbound-events`` 的 ``UNIQUE(fingerprint)``
        约束保证同一 cycle 不会被并发进程重复记录。失败不阻塞主流程。
        """
        record_fn = getattr(self.client, "inbound_event_record", None)
        if record_fn is None:
            # 测试 mock 可能不实现此方法；统计但不上报。
            stats.inbound_events_skipped = 1
            return
        fingerprint = f"simple-remind:{self.config.persona}:{now.strftime('%Y%m%dT%H%M%S%fZ')}"
        event_id = str(uuid.uuid4())
        try:
            recorded = record_fn(
                event_id=event_id,
                fingerprint=fingerprint,
                event_type="simple-waker.remind",
                source="polling",
            )
            if recorded:
                stats.inbound_events_recorded = 1
            else:
                stats.inbound_events_duplicate = 1
        except WorkerError as exc:
            # 审计写入失败不影响主流程；记录到 state 供运维排查。
            typer.echo(f"[simple-waker:inbound_event] {exc}", err=True)
            stats.inbound_events_skipped = 1
            persona_state = self._persona_state(self.config.persona)
            persona_state["last_inbound_event_error"] = str(exc)
            persona_state["last_inbound_event_error_at"] = now.isoformat()
            self._state_dirty = True

    def _apply_action_item_escalation(
        self,
        stats: SimpleWakerStats,
        *,
        todos: dict[str, Any],
        now: datetime,
    ) -> None:
        """推进 action_item 升级时间线（WAKE → mark-wake-sent，STALE → mark-stale）。

        simple-waker 是批量 remind，不像 legacy runtime-waker 逐项 fingerprint
        wake。但 action_item 的 ``wake_count`` 仍需在 remind 时推进，否则
        永远停在第一阶段。STALE 项标记后从下次 remind 候选里消失（服务端
        ``action_items`` todo 只返回 ``stale_at IS NULL`` 的项）。
        """
        items = todos.get("action_items") if isinstance(todos, dict) else None
        if not isinstance(items, list) or not items:
            return
        # 取 persona agent_id 用于 owner 过滤。身份已在 _run_once_async 的
        # _ensure_identity 从 work 快照解析并缓存（T03），这里直接复用，
        # 不再每周期多起一次 whoami 子进程。测试 mock 可能未初始化，兜底空。
        me = self._me or {}
        persona_agent_id = str(me.get("id") or "") or None
        decisions = scan_pending_action_items(
            items,
            persona_agent_id=persona_agent_id,
            now=now,
        )
        if not decisions:
            return
        mark_wake_fn = getattr(self.client, "action_mark_wake_sent", None)
        mark_stale_fn = getattr(self.client, "action_mark_stale", None)
        for item_id, decision in decisions:
            if decision == ActionItemWakeDecision.SKIP:
                stats.action_items_skip += 1
                continue
            if decision == ActionItemWakeDecision.STALE:
                if self.config.dry_run or mark_stale_fn is None:
                    stats.action_items_stale += 1
                    continue
                try:
                    mark_stale_fn(item_id)
                    stats.action_items_stale += 1
                except WorkerError as exc:
                    typer.echo(f"[simple-waker:mark-stale] {exc}", err=True)
                    stats.action_items_errors += 1
                continue
            # WAKE
            if self.config.dry_run or mark_wake_fn is None:
                stats.action_items_wake += 1
                continue
            try:
                mark_wake_fn(item_id)
                stats.action_items_wake += 1
            except WorkerError as exc:
                typer.echo(f"[simple-waker:mark-wake-sent] {exc}", err=True)
                stats.action_items_errors += 1

    def _ensure_identity(self, work: dict[str, Any] | None = None) -> None:
        """Resolve the persona agent identity, preferring the work snapshot.

        快路径取 ``work["agent"]``；仅当快照缺身份（老版本 server / 测试
        mock）时回退独立 whoami 子进程。结果缓存在 ``self._me`` 供本周期
        下游消费（action_item escalation 的 owner 过滤）。
        """
        me = (work or {}).get("agent")
        if not me or not me.get("id"):
            me = self.client.whoami()
        if not me or not me.get("id"):
            raise WorkerError(
                f"Could not resolve {self.config.persona} identity; run "
                f"`map --persona {self.config.persona} persona whoami` first"
            )
        self._me = me

    def _persona_state(self, persona: str) -> dict[str, Any]:
        personas = self.state.setdefault("personas", {})
        if persona not in personas or not isinstance(personas[persona], dict):
            personas[persona] = {}
        return personas[persona]

    async def _reset_runtime_session_if_contract_changed(self) -> None:
        persona_state = self._persona_state(self.config.persona)
        previous_hash = persona_state.get("runtime_contract_hash")
        if previous_hash == self._runtime_contract_hash:
            return
        has_resume_session = bool(
            persona_state.get("claude_session_id")
            or persona_state.get("runtime_session_id")
            or persona_state.get("cursor_agent_id")
        )
        if previous_hash is not None or has_resume_session:
            typer.echo(
                f"[simple-waker] runtime contract changed for {self.config.persona}; starting a fresh runtime session",
                err=True,
            )
            await self.backend.reset_session()
        persona_state["runtime_contract_hash"] = self._runtime_contract_hash
        persona_state["runtime_contract_version"] = RUNTIME_CONTRACT_VERSION
        persona_state["runtime_contract_updated_at"] = datetime.now(timezone.utc).isoformat()
        self._state_dirty = True
        self._save_state_if_needed(force=True)

    async def _reset_runtime_session_if_backend_changed(self) -> None:
        persona_state = self._persona_state(self.config.persona)
        previous = persona_state.get("runtime_backend")
        if not previous or previous == self.config.runtime:
            return
        typer.echo(
            f"[simple-waker] runtime backend changed {previous} -> {self.config.runtime}; "
            "starting a fresh runtime session",
            err=True,
        )
        await self.backend.reset_session()

    def _touch_busy(self, stats: SimpleWakerStats, *, now: datetime) -> None:
        """I2（A1）：标记本 waker 进入 busy session（remind → claude 调用）。

        写 waker state 文件 ``session_busy_since`` + ``busy_pid`` +
        ``busy_started_at``；PATCH server ``agents.last_busy_since``。
        失败兜底不阻塞主流程（心跳信号不能反过来拖死 remind）。
        """
        persona_state = self._persona_state(self.config.persona)
        persona_state["session_busy_since"] = now.isoformat()
        persona_state["busy_started_at"] = now.isoformat()
        persona_state["busy_pid"] = os.getpid()
        self._state_dirty = True
        self._save_state_if_needed(force=True)
        self._patch_server_busy(stats, busy_since=now)

    def _clear_busy(self, stats: SimpleWakerStats) -> None:
        """I2（A1）：与 ``_touch_busy`` 配对，会话结束清零 busy 标记。

        PID 自检：若 busy_pid != 当前 os.getpid() → 跨进程/重启场景，
        只清 server 列（让其他 worker 不被本地 stale 状态拖累），不动
        state 文件（避免误清别的进程 active busy 记录）。
        """
        persona_state = self._persona_state(self.config.persona)
        busy_pid = persona_state.get("busy_pid")
        own_pid = os.getpid()
        if busy_pid is not None and int(busy_pid) != own_pid:
            # 跨 PID：只清 server 列，本地 state 由 busy_pid 的进程负责。
            self._patch_server_busy(stats, busy_since=None)
            return
        for key in ("session_busy_since", "busy_started_at", "busy_pid"):
            persona_state.pop(key, None)
        self._state_dirty = True
        self._save_state_if_needed(force=True)
        self._patch_server_busy(stats, busy_since=None)

    # 实验 waker-status-view I2（A1+A3+A5）: 每个 cycle 自写 waker 状态
    # 指标（pid / cycles / reminds / skips / errors 滚动窗口），不引入
    # 新 IO（复用 _save_state_if_needed 路径 atomic write）。view 层派生
    # uptime/state/busy_since；本方法只写原始计数 + 时间戳。
    def _accumulate_cycle_stats(self, stats: SimpleWakerStats) -> None:
        persona_state = self._persona_state(self.config.persona)
        current_pid = os.getpid()
        now = datetime.now(timezone.utc)

        prev_pid = persona_state.get("pid")
        if prev_pid is not None and int(prev_pid) != current_pid:
            # A5: waker 重启（pid 变化）→ 归档旧计数 + 归零 cycles
            self._archive_and_reset_on_restart(
                persona_state, prev_pid=int(prev_pid), current_pid=current_pid, now=now
            )

        persona_state["pid"] = current_pid
        persona_state.setdefault("started_at", now.isoformat())

        persona_state["cycles_total"] = int(persona_state.get("cycles_total", 0)) + 1
        persona_state["reminds_sent_total"] = int(
            persona_state.get("reminds_sent_total", 0)
        ) + stats.reminds_sent
        persona_state["skips_unchanged_total"] = int(
            persona_state.get("skips_unchanged_total", 0)
        ) + stats.remind_skips_unchanged

        had_error = 1 if (stats.cycle_errors > 0 or stats.remind_errors > 0) else 0
        errors_window = list(persona_state.get("errors_last_n_window") or [])
        errors_window.append(had_error)
        if len(errors_window) > 10:
            errors_window = errors_window[-10:]
        persona_state["errors_last_n_window"] = errors_window
        persona_state["errors_last_n"] = sum(errors_window)

        persona_state["last_cycle_at"] = now.isoformat()
        persona_state["last_poll_at"] = now.isoformat()

        self._state_dirty = True

    def _archive_and_reset_on_restart(
        self,
        persona_state: dict[str, Any],
        *,
        prev_pid: int,
        current_pid: int,
        now: datetime,
    ) -> None:
        """A5: waker pid 变化 → 写 .stale.<ts>.json 归档旧计数 + 重置 cycles。

        主 state 文件保持原地（atomic write 契约）；sidecar 只记上次关键计数
        + pid diff 供事后溯源。runtime session 字段（claude_session_id /
        runtime_session_id / runtime_contract_hash）保留——重启不破坏既有
        runtime 状态机连续性。
        """
        state_file = self.config.state_file
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        archive_path = state_file.with_suffix(f".stale.{ts}.json")
        archive_marker = {
            "archived_at": now.isoformat(),
            "previous_pid": prev_pid,
            "new_pid": current_pid,
            "previous_cycles_total": int(persona_state.get("cycles_total", 0)),
            "previous_reminds_sent_total": int(
                persona_state.get("reminds_sent_total", 0)
            ),
            "previous_errors_last_n": int(persona_state.get("errors_last_n", 0)),
        }
        with contextlib.suppress(OSError):
            # 归档失败不阻塞主流程（best-effort）；view 仍能从 cycles=0 看到 restart
            archive_path.write_text(
                json.dumps(archive_marker, ensure_ascii=False, indent=2)
            )

        for key in (
            "cycles_total",
            "reminds_sent_total",
            "skips_unchanged_total",
            "errors_last_n_window",
            "errors_last_n",
            "started_at",
            "last_cycle_at",
            "last_poll_at",
            "busy_started_at",
            "session_busy_since",
            "busy_pid",
        ):
            persona_state.pop(key, None)

    def _check_busy_crash_recovery(self) -> None:
        """I2（A2 + A8 边界）：启动时回收上次 crash 残留 busy 标记。

        state 文件若含 ``busy_pid`` 且进程已死（kill -0 抛 ProcessLookupError）
        → 清零本地 state + server ``agents.last_busy_since``；进程仍活则
        视作并发 waker，不动（让对方的 _clear_busy 自己处理）。
        """
        persona_state = self._persona_state(self.config.persona)
        busy_pid_raw = persona_state.get("busy_pid")
        if busy_pid_raw is None:
            return
        try:
            busy_pid = int(busy_pid_raw)
        except (TypeError, ValueError):
            busy_pid = None
        if busy_pid is not None:
            try:
                os.kill(busy_pid, 0)
                # 进程仍活 → 视为并发 waker，不回收
                typer.echo(
                    f"[simple-waker] busy_pid={busy_pid} still alive; "
                    "skipping crash recovery",
                    err=True,
                )
                return
            except ProcessLookupError:
                pass  # 进程已死 → 回收
            except PermissionError:
                # 别人的 PID（EPERM）→ 不动，由对方负责清零
                typer.echo(
                    f"[simple-waker] busy_pid={busy_pid} not owned; "
                    "skipping crash recovery",
                    err=True,
                )
                return
            except OSError:
                return
        # 进程已死 → 清零本地 + 兜底清 server（best-effort，不抛错）
        for key in ("session_busy_since", "busy_started_at", "busy_pid"):
            persona_state.pop(key, None)
        self._state_dirty = True
        self._save_state_if_needed(force=True)
        # 启动时无 caller context，临时 stats 仅承载计数（_patch_server_busy
        # 当前不读 stats 字段，但保持参数形状一致便于将来加 metric）。
        self._patch_server_busy(SimpleWakerStats(), busy_since=None)
        typer.echo(
            "[simple-waker] recovered from prior crash; busy_since cleared",
            err=True,
        )

    def _patch_server_busy(
        self,
        stats: SimpleWakerStats,
        *,
        busy_since: datetime | None,
    ) -> None:
        """PATCH server ``agents.last_busy_since``（A1）。

        失败兜底（catch WorkerError），不阻塞主流程。心跳信号本身
        是 best-effort 可观测性扩展，server 短暂不可达不应反向
        拖累 remind。
        """
        heartbeat_fn = getattr(self.client, "agent_heartbeat", None)
        if heartbeat_fn is None:
            # 测试 mock 可能未实现此方法；不报错也不计入 stats。
            return
        try:
            heartbeat_fn(busy_since=busy_since)
        except WorkerError as exc:
            typer.echo(f"[simple-waker:heartbeat] {exc}", err=True)

    def _save_state_if_needed(self, *, force: bool = False) -> None:
        if not force and not self._state_dirty:
            return

        save_bridge_state(self.config.state_file, self.state)
        self._state_dirty = False


@APP.command()
def run(
    persona: str = typer.Option("host", "--persona", help="MAP persona to wake."),
    project_root: Path = typer.Option(Path("."), "--project-root", help="Project root containing .map/."),
    map_cmd: str = typer.Option("map", "--map-cmd", help="MAP CLI command."),
    active_interval: float = typer.Option(
        30.0,
        "--active-interval",
        min=5.0,
        help="Poll/remind cadence while work exists.",
    ),
    idle_interval: float = typer.Option(
        300.0,
        "--idle-interval",
        min=30.0,
        help="Poll cadence when idle.",
    ),
    min_remind_seconds: float = typer.Option(
        30.0,
        "--min-remind-seconds",
        min=5.0,
        help="Minimum seconds between remind prompts while work remains.",
    ),
    max_silence_seconds: float = typer.Option(
        1800.0,
        "--max-silence-seconds",
        min=60.0,
        help="工作集签名不变时的强制唤醒兜底间隔（秒）；签名去重后超过该时长未唤醒则兜底提醒一次。",
    ),
    once: bool = typer.Option(False, "--once", help="Run one cycle and exit."),
    max_cycles: int | None = typer.Option(None, "--max-cycles", min=1, help="Stop after N cycles."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print remind actions without invoking runtime."),
    drain_topics: bool = typer.Option(
        False,
        "--drain-topics",
        help="Host mode: keep reminding while any open topic exists; dismiss does not count as done.",
    ),
    state_file: Path | None = typer.Option(
        Path(".map/simple-waker-state.json"),
        "--state-file",
        help="Runtime session + remind timestamps.",
    ),
    model: str | None = typer.Option(None, "--model", help="Optional agent model override."),
    runtime: str | None = typer.Option(
        None,
        "--runtime",
        help="Agent runtime: claude (default) or cursor. Env: MAP_SIMPLE_RUNTIME.",
    ),
    runtime_home: Path | None = typer.Option(
        None,
        "--runtime-home",
        help="Optional HOME for the Claude runtime process (ignored for --runtime cursor).",
    ),
    stale_threshold_minutes: int | None = typer.Option(
        None,
        "--waker-stale-threshold",
        min=0,
        help=(
            "Stale-open-topic threshold (minutes). When set, exports "
            "MAP_STALE_OPEN_TOPIC_THRESHOLD_MINUTES so subprocess "
            "``map work`` invocations and any in-process server pick up "
            "the override via server.config.Settings (f873c287 I1(g))."
        ),
    ),
    max_prompt_topics: int = typer.Option(
        3,
        "--max-prompt-topics",
        min=0,
        help=(
            "Top-K quota: max topic obligations listed per remind prompt "
            "(obligation first, oldest first). Excess items are deferred to "
            "the next remind automatically. 0 = unlimited (legacy behavior). "
            "Env fallback: MAP_SIMPLE_MAX_PROMPT_TOPICS."
        ),
    ),
    subprocess_client: bool = typer.Option(
        False,
        "--subprocess-client",
        help=(
            "T24 rollback: talk to MAP via `map` subprocess instead of "
            "in-process SDK. Env: MAP_WAKER_SUBPROCESS=1."
        ),
    ),
    drift_check_interval_cycles: int = typer.Option(
        30,
        "--drift-check-interval-cycles",
        min=0,
        help=(
            "实验 waker-runtime-skill-hotcheck I4：每 N 个 poll cycle 跑一次"
            " runtime skill 漂移检测；0 = 关闭。"
            " Env fallback: WAKER_DRIFT_CHECK_INTERVAL_CYCLES。"
        ),
    ),
) -> None:
    """Run the simplified MAP waker loop."""
    root = project_root.resolve()
    try:
        resolved_runtime = resolve_waker_runtime(runtime)
    except WorkerError as exc:
        typer.echo(f"[simple-waker] {exc}", err=True)
        raise typer.Exit(code=2) from exc
    # LLM 凭据权威来源按 runtime 分流：Claude 走 .map/.claude-env，Cursor 走
    # .map/.cursor-env。直启（绕过 start-*.sh）也强制以文件为准，避免继承
    # shell 残留端点/账号。
    if resolved_runtime == "cursor":
        apply_project_cursor_env(root)
    else:
        apply_project_claude_env(root)
    resolved_runtime_home = runtime_home
    if resolved_runtime != "cursor" and resolved_runtime_home is not None and not dry_run:
        _startup_sync_with_audit(root, resolved_runtime_home)
    # f873c287 I1(g): apply threshold to env BEFORE any Settings read so
    # the ``map work`` subprocess (and any in-process server) sees the
    # override on its first ``get_settings()`` call. We export here even
    # in dry-run so logs reflect what the value would be in production.
    if stale_threshold_minutes is not None:
        os.environ["MAP_STALE_OPEN_TOPIC_THRESHOLD_MINUTES"] = str(stale_threshold_minutes)
        # If the server module is already imported in this process (test
        # fixtures), invalidate the lru_cache so the new value wins.
        try:
            from server.config import get_settings

            get_settings.cache_clear()
        except Exception:
            pass
    client = build_waker_client(
        persona=persona,
        project_root=root,
        map_cmd=map_cmd,
        dry_run=False,
        subprocess_client=subprocess_client,
    )
    # env fallback：不带 flag 启动（如 scripts/start-simple-waker.sh 直传旧参数）
    # 时仍可经 MAP_SIMPLE_MAX_PROMPT_TOPICS 调配额。
    if max_prompt_topics == 3:
        env_value = os.environ.get("MAP_SIMPLE_MAX_PROMPT_TOPICS")
        if env_value:
            try:
                max_prompt_topics = max(0, int(env_value))
            except ValueError:
                typer.echo(
                    f"[simple-waker] ignore invalid MAP_SIMPLE_MAX_PROMPT_TOPICS={env_value!r}",
                    err=True,
                )
    # I4: drift check 周期默认 30；env WAKER_DRIFT_CHECK_INTERVAL_CYCLES 覆盖。
    if drift_check_interval_cycles == 30:
        env_value = os.environ.get("WAKER_DRIFT_CHECK_INTERVAL_CYCLES")
        if env_value:
            try:
                drift_check_interval_cycles = max(0, int(env_value))
            except ValueError:
                typer.echo(
                    f"[simple-waker] ignore invalid WAKER_DRIFT_CHECK_INTERVAL_CYCLES={env_value!r}",
                    err=True,
                )
    config = SimpleWakerConfig(
        persona=persona,
        project_root=root,
        map_cmd=map_cmd,
        active_interval=active_interval,
        idle_interval=idle_interval,
        once=once,
        max_cycles=max_cycles,
        dry_run=dry_run,
        state_file=state_file,
        model=model,
        runtime=resolved_runtime,
        runtime_home=resolved_runtime_home,
        min_remind_seconds=min_remind_seconds,
        max_silence_seconds=max_silence_seconds,
        drain_topics=drain_topics,
        max_prompt_topics=max_prompt_topics,
        stale_threshold_minutes=stale_threshold_minutes,
        drift_check_interval_cycles=drift_check_interval_cycles,
    )
    waker = SimpleWaker(client=client, config=config)
    waker.run_forever()


if __name__ == "__main__":
    APP()
