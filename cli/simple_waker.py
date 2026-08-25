"""Thin MAP waker: poll unified work snapshot, remind agent when there is new work.

Waker logic stays minimal: the platform serves ``GET /agents/me/work``
(whoami + topic-progress + todos + wakeable notifications); agents use
``map work`` / ``map topic progress`` in Skills.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
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
from cli.host_worker_types import WorkerError
from cli.map_command_client import MapCommandClient
from cli.wake_backend import (
    TODO_BUCKET_UI_LABELS,
    TODO_WAKE_BUCKETS,
    PersonaAgentWakeBackend,
    sync_runtime_skills,
)
from cli.worker_cycle_log import log_cycle_summary

APP = typer.Typer(add_completion=False)

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
        return (
            self.topic_update_count
            + self.todo_item_count
            + self.notification_count
            + self.open_topic_count
        )

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
    runtime_home: Path | None = None
    min_remind_seconds: float = 30.0
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


@dataclass
class SimpleWakerStats:
    cycles: int = 0
    polls_with_work: int = 0
    polls_idle: int = 0
    reminds_sent: int = 0
    remind_skips_busy: int = 0
    remind_skips_cooldown: int = 0
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
                    str(raw["last_comment_author_name"])
                    if raw.get("last_comment_author_name")
                    else None
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
        items = [
            item
            for item in raw_items
            if isinstance(item, dict) and _todo_item_is_actionable(kind, item)
        ]
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
    deduped_notifications, event_count_sum, dropped = _dedupe_notifications_by_group_key(
        notifications
    )
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


def should_send_remind(
    context: WakeContext,
    *,
    now: datetime,
    last_remind_at: datetime | None,
    inflight: bool,
    min_remind_seconds: float,
) -> tuple[bool, str | None]:
    if not context.has_work:
        return False, "idle"
    if inflight:
        return False, "busy"
    if last_remind_at is not None:
        elapsed = (now - last_remind_at).total_seconds()
        if elapsed < min_remind_seconds:
            return False, "cooldown"
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
            lines.append(
                f"- notification: {context.notification_count}"
                f"（{TODO_BUCKET_UI_LABELS['notification']}）"
            )
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
        client: MapCommandClient,
        config: SimpleWakerConfig | None = None,
        backend: PersonaAgentWakeBackend | None = None,
    ) -> None:
        self.client = client
        self.config = config or SimpleWakerConfig()
        self.state = load_bridge_state(
            self.config.state_file,
            bridge_name="simple-waker",
            default_collections=("personas",),
        )
        self._state_dirty = False
        self._inflight = False
        # T03：本周期解析出的 persona 身份（work 快照的 agent 字段）。
        # 缓存后 action_item escalation 等下游消费点不再重复调 whoami 子进程。
        self._me: dict[str, Any] | None = None
        self.backend = backend or PersonaAgentWakeBackend(
            project_root=self.config.project_root,
            persona=self.config.persona,
            get_agent_state=lambda: self._persona_state(self.config.persona),
            save_state_fn=lambda: self._save_state_if_needed(force=True),
            model=self.config.model,
            runtime_home=self.config.runtime_home,
        )
        self._runtime_contract_hash = runtime_contract_hash(self.config.project_root)

    def run_forever(self) -> SimpleWakerStats:
        return asyncio.run(self._run_forever_async())

    async def _run_forever_async(self) -> SimpleWakerStats:
        if not self.config.dry_run:
            await self._reset_runtime_session_if_contract_changed()
        if not self.config.dry_run:
            await self.backend.connect()
        total = SimpleWakerStats()
        try:
            while True:
                try:
                    stats, sleep_for = await self._run_once_async()
                except WorkerError as exc:
                    # 瞬时错误兜底：API 5xx / 子进程失败 / 身份解析失败等。
                    # 长驻 waker 不能因单次 cycle 失败退出——记错到 state，
                    # 按 idle 间隔退避后下一 cycle 重试。持续失败会在 state
                    # 累积 last_cycle_error 供运维观测。
                    typer.echo(f"[simple-waker:cycle-error] {exc}", err=True)
                    persona_state = self._persona_state(self.config.persona)
                    persona_state["last_cycle_error"] = str(exc)
                    persona_state["last_cycle_error_at"] = datetime.now(timezone.utc).isoformat()
                    self._state_dirty = True
                    self._save_state_if_needed(force=True)
                    stats = SimpleWakerStats(cycles=1, cycle_errors=1)
                    sleep_for = self.config.idle_interval
                total.add(stats)
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
                await asyncio.sleep(sleep_for)
            return total
        finally:
            if not self.config.dry_run:
                await self.backend.disconnect()

    def run_once(self) -> SimpleWakerStats:
        return asyncio.run(self._run_once_async())[0]

    async def _run_once_async(self) -> tuple[SimpleWakerStats, float]:
        stats = SimpleWakerStats(cycles=1)
        self._scan_stalled_experiment_locks(stats)
        work = self.client.work() or {}
        # T03：work 快照本身含完整 agent 身份（AgentWorkRead.agent），且认证
        # 失败时 work 子进程同样非零退出——身份直接从快照取，省掉每周期一次
        # 独立的 whoami 子进程（完整 Python + Typer 冷启动）。
        self._ensure_identity(work)
        topic_progress_data = work.get("topic_progress") or {}
        todos = work.get("todos") or {}
        notifications_payload = work.get("notifications") or {}
        notifications = (
            list(notifications_payload.get("items") or [])
            if isinstance(notifications_payload, dict)
            else []
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
        should_remind, skip_reason = should_send_remind(
            context,
            now=now,
            last_remind_at=last_remind_at,
            inflight=self._inflight,
            min_remind_seconds=self.config.min_remind_seconds,
        )
        if not should_remind:
            if skip_reason == "busy":
                stats.remind_skips_busy = 1
            elif skip_reason == "cooldown":
                stats.remind_skips_cooldown = 1
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
        try:
            await self.backend.wake_async(prompt=prompt, event_source="simple-waker")
            persona_state["last_remind_at"] = now.isoformat()
            persona_state["last_remind_work_count"] = context.total_items
            persona_state["last_remind_topic_count"] = context.topic_update_count
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
            persona_state.get("claude_session_id") or persona_state.get("runtime_session_id")
        )
        if previous_hash is not None or has_resume_session:
            typer.echo(
                f"[simple-waker] runtime contract changed for {self.config.persona}; "
                "starting a fresh runtime session",
                err=True,
            )
            await self.backend.reset_session()
        persona_state["runtime_contract_hash"] = self._runtime_contract_hash
        persona_state["runtime_contract_version"] = RUNTIME_CONTRACT_VERSION
        persona_state["runtime_contract_updated_at"] = datetime.now(timezone.utc).isoformat()
        self._state_dirty = True
        self._save_state_if_needed(force=True)

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
    model: str | None = typer.Option(None, "--model", help="Optional Claude model override."),
    runtime_home: Path | None = typer.Option(
        None,
        "--runtime-home",
        help="Optional HOME for the Claude runtime process.",
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
) -> None:
    """Run the simplified MAP waker loop."""
    root = project_root.resolve()
    # LLM 凭据权威来源 .map/.claude-env：即使被直启（绕过 start-*.sh 的
    # source），也强制走项目代理端点/模型，避免继承 z.ai 等 shell 残留导致
    # 5 小时 429 用量上限（见 33712fe 之后的补漏）。
    apply_project_claude_env(root)
    resolved_runtime_home = runtime_home
    if resolved_runtime_home is not None and not dry_run:
        sync_runtime_skills(project_root=root, runtime_home=resolved_runtime_home)
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
    client = MapCommandClient(persona=persona, project_root=root, map_cmd=map_cmd)
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
        runtime_home=resolved_runtime_home,
        min_remind_seconds=min_remind_seconds,
        drain_topics=drain_topics,
        max_prompt_topics=max_prompt_topics,
        stale_threshold_minutes=stale_threshold_minutes,
    )
    waker = SimpleWaker(client=client, config=config)
    waker.run_forever()


if __name__ == "__main__":
    APP()
