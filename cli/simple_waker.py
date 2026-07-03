"""Thin MAP waker: poll todos, remind agent on a fixed cadence while work exists.

Unlike ``cli.runtime_waker``, this module does not derive per-item fingerprints,
SSE routing, inbound_event dedup, or one-item-per-wake scheduling. A single
long-lived runtime session per persona receives a unified reminder whenever
``map todos`` or unread notifications show pending work.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
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


@dataclass(frozen=True)
class PendingBucket:
    kind: str
    count: int
    label: str


@dataclass(frozen=True)
class PendingWorkSummary:
    buckets: tuple[PendingBucket, ...]
    notification_count: int

    @property
    def total_items(self) -> int:
        return sum(bucket.count for bucket in self.buckets) + self.notification_count

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


def summarize_pending_work(
    todos: dict[str, Any],
    *,
    notifications: list[dict[str, Any]] | None = None,
) -> PendingWorkSummary:
    buckets: list[PendingBucket] = []
    for kind in TODO_WAKE_BUCKETS:
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
    notification_count = len(notifications or [])
    return PendingWorkSummary(buckets=tuple(buckets), notification_count=notification_count)


def should_send_remind(
    summary: PendingWorkSummary,
    *,
    now: datetime,
    last_remind_at: datetime | None,
    inflight: bool,
    min_remind_seconds: float,
) -> tuple[bool, str | None]:
    if not summary.has_work:
        return False, "idle"
    if inflight:
        return False, "busy"
    if last_remind_at is not None:
        elapsed = (now - last_remind_at).total_seconds()
        if elapsed < min_remind_seconds:
            return False, "cooldown"
    return True, None


def build_remind_prompt(persona: str, summary: PendingWorkSummary) -> str:
    command = f"map --persona {persona}"
    lines = [
        f"MAP 协作提醒 · {persona}",
        "",
        "请检查 MAP 平台当前待办并自主处理：",
        "1. 先读 map-runtime-waker、map-project-collab 与 persona Skill",
        f"2. `{command} persona whoami` → `{command} todos`",
        "3. 可批量处理相关待办；以 `map todos` 为空或每项有明确处置为准",
        "4. 禁止凭 session 记忆跳过待办；清理方式与 Web UI 相同",
        "",
        "当前待办概览：",
    ]
    for bucket in summary.buckets:
        lines.append(f"- {bucket.kind}: {bucket.count}（{bucket.label}）")
    if summary.notification_count:
        lines.append(
            f"- notification: {summary.notification_count}"
            f"（{TODO_BUCKET_UI_LABELS['notification']}）"
        )
    if not summary.buckets and summary.notification_count:
        lines.append("- 仅有未读通知，请先 `todos` 再按通知类型处理")
    return "\n".join(lines) + "\n"


def next_sleep_seconds(summary: PendingWorkSummary, config: SimpleWakerConfig) -> float:
    return config.active_interval if summary.has_work else config.idle_interval


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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
            await self.backend.disconnect()

    def run_once(self) -> SimpleWakerStats:
        return asyncio.run(self._run_once_async())[0]

    async def _run_once_async(self) -> tuple[SimpleWakerStats, float]:
        self._ensure_identity()
        stats = SimpleWakerStats(cycles=1)
        todos = self.client.todos() or {}
        notifications = self.client.notifications_unread()
        summary = summarize_pending_work(todos, notifications=notifications)
        if summary.has_work:
            stats.polls_with_work = 1
        else:
            stats.polls_idle = 1

        persona_state = self._persona_state(self.config.persona)
        last_remind_at = _parse_datetime(persona_state.get("last_remind_at"))
        now = datetime.now(UTC)
        should_remind, skip_reason = should_send_remind(
            summary,
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
            return stats, next_sleep_seconds(summary, self.config)

        prompt = build_remind_prompt(self.config.persona, summary)
        if self.config.dry_run:
            typer.echo(f"[dry-run] would remind persona={self.config.persona} work={summary.total_items}")
            typer.echo(prompt.rstrip())
            stats.dry_run_actions = 1
            self._save_state_if_needed()
            return stats, next_sleep_seconds(summary, self.config)

        self._inflight = True
        try:
            await self.backend.wake_async(prompt=prompt, event_source="simple-waker")
            persona_state["last_remind_at"] = now.isoformat()
            persona_state["last_remind_work_count"] = summary.total_items
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
        return stats, next_sleep_seconds(summary, self.config)

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
        help="Poll/remind cadence while todos or notifications are pending.",
    ),
    idle_interval: float = typer.Option(
        300.0,
        "--idle-interval",
        min=30.0,
        help="Poll cadence when there is no pending work.",
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
    if resolved_runtime_home is not None:
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
