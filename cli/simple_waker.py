"""Thin MAP waker: poll unified work snapshot, remind agent when there is new work.

Waker logic stays minimal: the platform serves ``GET /agents/me/work``
(whoami + topic-progress + todos + wakeable notifications); agents use
``map work`` / ``map topic progress`` in Skills.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from cli.bridge_state import load_bridge_state, save_bridge_state
from cli.host_worker_types import WorkerError
from cli.map_command_client import MapCommandClient
from cli.runtime_waker import (
    PersonaAgentWakeBackend,
    TODO_BUCKET_UI_LABELS,
    TODO_WAKE_BUCKETS,
    sync_runtime_skills,
)
from cli.worker_cycle_log import log_cycle_summary

APP = typer.Typer(add_completion=False)

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


@dataclass(frozen=True)
class PendingBucket:
    kind: str
    count: int
    label: str


@dataclass(frozen=True)
class WakeContext:
    topic_progress: tuple[TopicProgressEntry, ...] = ()
    todo_buckets: tuple[PendingBucket, ...] = ()
    notification_count: int = 0

    @property
    def topic_update_count(self) -> int:
        return len(self.topic_progress)

    @property
    def todo_item_count(self) -> int:
        return sum(bucket.count for bucket in self.todo_buckets)

    @property
    def total_items(self) -> int:
        return self.topic_update_count + self.todo_item_count + self.notification_count

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

    def add(self, other: SimpleWakerStats) -> None:
        self.cycles += other.cycles
        self.polls_with_work += other.polls_with_work
        self.polls_idle += other.polls_idle
        self.reminds_sent += other.reminds_sent
        self.remind_skips_busy += other.remind_skips_busy
        self.remind_skips_cooldown += other.remind_skips_cooldown
        self.remind_errors += other.remind_errors
        self.dry_run_actions += other.dry_run_actions


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
        if isinstance(work_items, list):
            for item in work_items:
                if isinstance(item, dict) and item.get("kind"):
                    kinds.append(str(item["kind"]))
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
            )
        )
    return tuple(entries)


def summarize_actionable_todos(todos: dict[str, Any]) -> tuple[PendingBucket, ...]:
    buckets: list[PendingBucket] = []
    for kind in TODO_WAKE_BUCKETS:
        if kind in SIMPLE_WAKER_PASSIVE_BUCKETS:
            continue
        items = todos.get(kind) or []
        if not isinstance(items, list):
            continue
        count = sum(1 for item in items if isinstance(item, dict))
        if count <= 0:
            continue
        buckets.append(
            PendingBucket(
                kind=kind,
                count=count,
                label=TODO_BUCKET_UI_LABELS.get(kind, kind),
            )
        )
    return tuple(buckets)


def build_wake_context(
    *,
    topic_progress_data: dict[str, Any] | None,
    todos: dict[str, Any],
    notifications: list[dict[str, Any]] | None = None,
    persona: str | None = None,
) -> WakeContext:
    filtered_progress = _filter_topic_progress_for_persona(
        persona,
        topic_progress_data,
        todos,
    )
    return WakeContext(
        topic_progress=parse_topic_progress(filtered_progress),
        todo_buckets=summarize_actionable_todos(todos),
        notification_count=len(notifications or []),
    )


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
        "平台检测到待处理 work items。请先读 map-runtime-waker、map-project-collab 与 persona Skill，然后：",
        f"1. `{command} persona whoami`",
        f"2. `{command} work` 或 `{command} topic progress` — topic work items 统一视图（obligation + contextual；与 todos 话题分区同源）",
        f"3. `{command} todos` — 实验/评审/mention 等待办分区",
        "4. 按 work_items.kind 逐项处理（obligation 优先）；host 回复 thread 与推进轮次",
        "",
    ]

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
        lines.append("")

    if context.todo_buckets or context.notification_count:
        lines.append("## 其他待办")
        for bucket in context.todo_buckets:
            lines.append(f"- {bucket.kind}: {bucket.count}（{bucket.label}）")
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
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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
        self.backend = backend or PersonaAgentWakeBackend(
            project_root=self.config.project_root,
            persona=self.config.persona,
            get_agent_state=lambda: self._persona_state(self.config.persona),
            save_state_fn=lambda: self._save_state_if_needed(force=True),
            model=self.config.model,
            runtime_home=self.config.runtime_home,
        )

    def run_forever(self) -> SimpleWakerStats:
        return asyncio.run(self._run_forever_async())

    async def _run_forever_async(self) -> SimpleWakerStats:
        if not self.config.dry_run:
            await self.backend.connect()
        total = SimpleWakerStats()
        try:
            while True:
                stats, sleep_for = await self._run_once_async()
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
        self._ensure_identity()
        stats = SimpleWakerStats(cycles=1)
        work = self.client.work() or {}
        topic_progress_data = work.get("topic_progress") or {}
        todos = work.get("todos") or {}
        notifications_payload = work.get("notifications") or {}
        notifications = (
            list(notifications_payload.get("items") or [])
            if isinstance(notifications_payload, dict)
            else []
        )
        context = build_wake_context(
            topic_progress_data=topic_progress_data,
            todos=todos,
            notifications=notifications,
            persona=self.config.persona,
        )
        if context.has_work:
            stats.polls_with_work = 1
        else:
            stats.polls_idle = 1

        persona_state = self._persona_state(self.config.persona)
        last_remind_at = _parse_datetime(persona_state.get("last_remind_at"))
        now = datetime.now(UTC)
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

        self._inflight = True
        try:
            await self.backend.wake_async(prompt=prompt, event_source="simple-waker")
            persona_state["last_remind_at"] = now.isoformat()
            persona_state["last_remind_work_count"] = context.total_items
            persona_state["last_remind_topic_count"] = context.topic_update_count
            self._state_dirty = True
            stats.reminds_sent = 1
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

    def _ensure_identity(self) -> None:
        me = self.client.whoami()
        if not me or not me.get("id"):
            raise WorkerError(
                f"Could not resolve {self.config.persona} identity; run "
                f"`map --persona {self.config.persona} persona whoami` first"
            )

    def _persona_state(self, persona: str) -> dict[str, Any]:
        personas = self.state.setdefault("personas", {})
        if persona not in personas or not isinstance(personas[persona], dict):
            personas[persona] = {}
        return personas[persona]

    def _save_state_if_needed(self, *, force: bool = False) -> None:
        if not force and not self._state_dirty:
            return
        from cli.bridge_state import save_bridge_state

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
) -> None:
    """Run the simplified MAP waker loop."""
    root = project_root.resolve()
    resolved_runtime_home = runtime_home
    if resolved_runtime_home is not None and not dry_run:
        sync_runtime_skills(project_root=root, runtime_home=resolved_runtime_home)
    client = MapCommandClient(persona=persona, project_root=root, map_cmd=map_cmd)
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
    )
    waker = SimpleWaker(client=client, config=config)
    waker.run_forever()


if __name__ == "__main__":
    APP()
