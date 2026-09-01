"""Waker 工作集解析与 wake 决策 — T45 拆分自 cli/simple_waker.py。

纯函数层：work 快照 → :class:`WakeContext`（话题义务 / todo 分桶 / 通知
去重）→ 签名去重（``wake_signature`` / ``should_send_remind``）→ remind
prompt 构建。``SimpleWaker`` 主循环留守宿主 ``cli.simple_waker``（测试
monkeypatch 面：``build_wake_backend`` / ``SimpleWaker.run_forever``）。
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cli.map_command_client import MapCommandClient
from cli.map_sdk_client import MapSdkClient
from cli.wake_backend import TODO_BUCKET_UI_LABELS, TODO_WAKE_BUCKETS

# Passive inventory — not used alone to wake (topic activity uses topic-progress).
SIMPLE_WAKER_PASSIVE_BUCKETS: frozenset[str] = frozenset({"my_open_topics"})

_EXCERPT_IN_PROMPT = 280


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
    # 话题切换触发的 runtime session 重置次数（2026-08-31 成本战役结论：
    # 会话以话题为边界复用；唤醒工作集话题 id 集合变化 → reset_session
    # 后再唤醒，旧话题完整历史不背进新会话）。
    session_resets_topic_switch: int = 0

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
        "4. 按 work_items.kind 或 todos.actions 逐项处理（obligation 优先）；topic 用 topic-host，pending_reviews 用 experiment-reviewer，my_open_experiments 用 experiment-host，executor_assignments 用 experiment-executor",
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


def summarize_pending_work(
    todos: dict[str, Any], notifications: list[dict[str, Any]] | None = None
) -> WakeContext:
    return build_wake_context(
        topic_progress_data={"items": []},
        todos=todos,
        notifications=notifications,
        persona=None,
    )
