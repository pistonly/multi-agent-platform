from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import typer

from cli.agent_client import PersonaAgentClient
from cli.bridge_state import load_bridge_state, save_bridge_state
from cli.host_worker_types import WorkerError
from cli.map_command_client import MapCommandClient
from cli.worker_cycle_log import log_cycle_summary

APP = typer.Typer(add_completion=False)


@dataclass(frozen=True)
class WakeEvent:
    persona: str
    kind: str
    object_id: str
    fingerprint: str
    title: str | None = None
    reason: str | None = None
    payload: dict[str, Any] | None = None


@dataclass
class WakeResult:
    session_id: str | None = None
    response_text: str | None = None
    skipped: bool = False


@dataclass
class RuntimeWakerConfig:
    persona: str = "host"
    interval: float = 30.0
    once: bool = False
    max_cycles: int | None = None
    dry_run: bool = False
    max_wakes_per_cycle: int = 3
    cooldown_seconds: float = 300.0
    state_file: Path | None = Path(".map/runtime-waker-state.json")
    project_root: Path = Path.cwd()
    map_cmd: str = "map"
    backend: str = "claude"
    model: str | None = None
    runtime_home: Path | None = None
    codex_bin: str | None = None
    force: bool = False
    include_participant_open_topics: bool = False


@dataclass
class RuntimeWakerStats:
    cycles: int = 0
    events_seen: int = 0
    wakes_sent: int = 0
    wake_skips: int = 0
    wake_errors: int = 0
    dry_run_actions: int = 0

    def add(self, other: "RuntimeWakerStats") -> None:
        self.cycles += other.cycles
        self.events_seen += other.events_seen
        self.wakes_sent += other.wakes_sent
        self.wake_skips += other.wake_skips
        self.wake_errors += other.wake_errors
        self.dry_run_actions += other.dry_run_actions


class WakeBackend(Protocol):
    def wake(
        self,
        *,
        persona: str,
        prompt: str,
        session_id: str | None,
    ) -> WakeResult:
        ...


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

    async def wake_async(self, *, prompt: str) -> WakeResult:
        if self._agent_client is None:
            await self.connect()
        assert self._agent_client is not None
        status = await self._agent_client.wake_up(prompt)
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

    def wake(
        self,
        *,
        persona: str,
        prompt: str,
        session_id: str | None,
    ) -> WakeResult:
        del persona, session_id
        return asyncio.run(self.wake_async(prompt=prompt))


class CodexSdkWakeBackend:
    def __init__(
        self,
        *,
        project_root: Path,
        runtime_home: Path | None = None,
        model: str | None = None,
        codex_bin: str | None = None,
    ) -> None:
        self.project_root = project_root
        self.runtime_home = runtime_home
        self.model = model
        self.codex_bin = codex_bin

    def wake(
        self,
        *,
        persona: str,
        prompt: str,
        session_id: str | None,
    ) -> WakeResult:
        del persona
        try:
            from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox, SkillInput, TextInput
        except ImportError as exc:  # pragma: no cover
            raise WorkerError("Install openai-codex to use the codex runtime backend") from exc

        env = dict(os.environ)
        if self.runtime_home is not None:
            env["CODEX_HOME"] = str(self.runtime_home)
        config = CodexConfig(
            codex_bin=self.codex_bin,
            cwd=str(self.project_root),
            env=env,
            config_overrides=(
                'sandbox_mode="workspace-write"',
                'approval_policy="never"',
            ),
        )

        input_items: list[Any] = [TextInput(prompt)]
        skill_path = self.project_root / ".cursor" / "skills" / "map-runtime-waker" / "SKILL.md"
        if skill_path.is_file():
            input_items.insert(0, SkillInput(name="map-runtime-waker", path=str(skill_path)))

        with Codex(config=config) as codex:
            thread_kwargs = {
                "cwd": str(self.project_root),
                "model": self.model,
                "sandbox": Sandbox.workspace_write,
                "approval_mode": ApprovalMode.deny_all,
            }
            if session_id:
                thread = codex.thread_resume(session_id, **thread_kwargs)
            else:
                thread = codex.thread_start(**thread_kwargs)
            result = thread.run(input_items, **thread_kwargs)

        return WakeResult(session_id=thread.id, response_text=result.final_response)


class RuntimeWaker:
    def __init__(
        self,
        *,
        client: MapCommandClient,
        config: RuntimeWakerConfig | None = None,
        backend: WakeBackend | None = None,
    ) -> None:
        self.client = client
        self.config = config or RuntimeWakerConfig()
        self.state = load_bridge_state(
            self.config.state_file,
            bridge_name="runtime-waker",
            default_collections=("personas",),
        )
        self._state_dirty = False
        self.agent_id: str | None = None
        if backend is not None:
            self.backend = backend
        elif self.config.backend == "claude":
            self.backend = PersonaAgentWakeBackend(
                project_root=self.config.project_root,
                persona=self.config.persona,
                get_agent_state=lambda: self._agent_state(self.config.persona),
                save_state_fn=lambda: self._save_state_if_needed(force=True),
                model=self.config.model,
                runtime_home=self.config.runtime_home,
            )
        else:
            self.backend = create_backend(self.config)

    def run_forever(self) -> RuntimeWakerStats:
        if isinstance(self.backend, PersonaAgentWakeBackend):
            return asyncio.run(self._run_forever_claude())
        return self._run_forever_sync()

    def _run_forever_sync(self) -> RuntimeWakerStats:
        total = RuntimeWakerStats()
        while True:
            stats = self.run_once()
            total.add(stats)
            log_cycle_summary(
                "runtime-waker",
                total,
                fields=[
                    "cycles",
                    "events_seen",
                    "wakes_sent",
                    "wake_skips",
                    "wake_errors",
                    "dry_run_actions",
                ],
            )
            if self.config.once:
                break
            if self.config.max_cycles is not None and total.cycles >= self.config.max_cycles:
                break
            time.sleep(self.config.interval)
        return total

    async def _run_forever_claude(self) -> RuntimeWakerStats:
        assert isinstance(self.backend, PersonaAgentWakeBackend)
        await self.backend.connect()
        try:
            total = RuntimeWakerStats()
            while True:
                stats = await self._run_once_async()
                total.add(stats)
                log_cycle_summary(
                    "runtime-waker",
                    total,
                    fields=[
                        "cycles",
                        "events_seen",
                        "wakes_sent",
                        "wake_skips",
                        "wake_errors",
                        "dry_run_actions",
                    ],
                )
                if self.config.once:
                    break
                if self.config.max_cycles is not None and total.cycles >= self.config.max_cycles:
                    break
                await asyncio.sleep(self.config.interval)
            return total
        finally:
            await self.backend.disconnect()

    def run_once(self) -> RuntimeWakerStats:
        return asyncio.run(self._run_once_async())

    async def _run_once_async(self) -> RuntimeWakerStats:
        self._ensure_identity()
        stats = RuntimeWakerStats(cycles=1)
        todos = self.client.todos() or {}
        if self.config.persona == "participant" and self.config.include_participant_open_topics:
            todos = {**todos, "open_topics": self.client.topic_list_open()}
        events = discover_wake_events(
            self.config.persona,
            todos,
            include_participant_open_topics=self.config.include_participant_open_topics,
        )
        stats.events_seen = len(events)

        wakes = 0
        for event in events:
            if wakes >= self.config.max_wakes_per_cycle:
                break
            if not self.config.force and self._should_skip_event(event):
                stats.wake_skips += 1
                continue
            if self.config.dry_run:
                typer.echo(f"[dry-run] would wake persona={event.persona} event={event.fingerprint}")
                stats.dry_run_actions += 1
                wakes += 1
                continue
            try:
                await self._wake_event(event)
            except WorkerError as exc:
                self._mark_event(event, status="error", error=str(exc))
                stats.wake_errors += 1
                continue
            stats.wakes_sent += 1
            wakes += 1

        self._save_state_if_needed()
        return stats

    def _ensure_identity(self) -> None:
        if self.agent_id is not None:
            return
        me = self.client.whoami()
        if not me or not me.get("id"):
            raise WorkerError(
                f"Could not resolve {self.config.persona} identity; run "
                f"`map --persona {self.config.persona} persona whoami` first"
            )
        self.agent_id = str(me["id"])

    async def _wake_event(self, event: WakeEvent) -> None:
        persona_state = self._persona_state(event.persona)
        session_id = self._session_id(persona_state)
        prompt = build_wake_prompt(event, project_root=self.config.project_root)
        if isinstance(self.backend, PersonaAgentWakeBackend):
            result = await self.backend.wake_async(prompt=prompt)
        else:
            result = self.backend.wake(persona=event.persona, prompt=prompt, session_id=session_id)
        if result.skipped:
            self._mark_event(event, status="runtime_skip")
            return
        if result.session_id:
            persona_state["runtime_session_id"] = result.session_id
            persona_state["claude_session_id"] = result.session_id
            persona_state["last_session_at"] = datetime.now(UTC).isoformat()
        self._mark_event(event, status="woken")

    def _session_id(self, persona_state: dict[str, Any]) -> str | None:
        sid = persona_state.get("claude_session_id") or persona_state.get("runtime_session_id")
        if sid and not persona_state.get("claude_session_id"):
            persona_state["claude_session_id"] = sid
        return str(sid) if sid else None

    def _agent_state(self, persona: str) -> dict[str, Any]:
        return self._persona_state(persona)

    def _should_skip_event(self, event: WakeEvent) -> bool:
        record = self._event_state(event)
        if not record:
            return False
        if record.get("status") == "woken":
            return True
        last_attempt = _parse_datetime(str(record.get("last_attempt_at") or ""))
        if last_attempt is None:
            return False
        return (datetime.now(UTC) - last_attempt).total_seconds() < self.config.cooldown_seconds

    def _persona_state(self, persona: str) -> dict[str, Any]:
        personas = self.state.setdefault("personas", {})
        if not isinstance(personas, dict):
            personas = {}
            self.state["personas"] = personas
        persona_state = personas.setdefault(persona, {})
        if not isinstance(persona_state, dict):
            persona_state = {}
            personas[persona] = persona_state
        persona_state.setdefault("events", {})
        return persona_state

    def _event_state(self, event: WakeEvent) -> dict[str, Any]:
        events = self._persona_state(event.persona).setdefault("events", {})
        if not isinstance(events, dict):
            return {}
        record = events.get(event.fingerprint)
        return record if isinstance(record, dict) else {}

    def _mark_event(self, event: WakeEvent, *, status: str, error: str | None = None) -> None:
        persona_state = self._persona_state(event.persona)
        events = persona_state.setdefault("events", {})
        if not isinstance(events, dict):
            events = {}
            persona_state["events"] = events
        record = events.setdefault(event.fingerprint, {})
        if not isinstance(record, dict):
            record = {}
            events[event.fingerprint] = record
        now = datetime.now(UTC).isoformat()
        record.update(
            {
                "status": status,
                "last_attempt_at": now,
                "kind": event.kind,
                "object_id": event.object_id,
                "title": event.title,
            }
        )
        if error:
            record["error"] = error
        elif "error" in record:
            del record["error"]
        persona_state["last_wake_at"] = now
        self._state_dirty = True

    def _save_state_if_needed(self, *, force: bool = False) -> None:
        if not force and not self._state_dirty:
            return
        save_bridge_state(self.config.state_file, self.state)
        self._state_dirty = False


def create_backend(config: RuntimeWakerConfig) -> WakeBackend:
    if config.backend == "codex":
        return CodexSdkWakeBackend(
            project_root=config.project_root,
            runtime_home=config.runtime_home,
            model=config.model,
            codex_bin=config.codex_bin,
        )
    raise WorkerError(f"Unsupported runtime backend: {config.backend}")


def discover_wake_events(
    persona: str,
    todos: dict[str, Any],
    *,
    include_participant_open_topics: bool = False,
) -> list[WakeEvent]:
    if persona == "host":
        return _host_events(todos)
    if persona == "participant":
        return _participant_events(todos, include_open_topics=include_participant_open_topics)
    if persona == "reviewer":
        return _reviewer_events(todos)
    return _generic_events(persona, todos)


def build_wake_prompt(event: WakeEvent, *, project_root: Path) -> str:
    command = f"map --persona {event.persona}"
    event_json = json.dumps(asdict(event), ensure_ascii=False, indent=2)
    return f"""你是 MAP 项目 `{project_root.name}` 的 `{event.persona}` persona。

这是一条 runtime wake-up。请恢复已有上下文后，自己读取项目 Skills 并用 MAP CLI 处理相关工作。

硬性规则：
- 先执行 `{command} persona whoami` 确认身份。
- 只使用 `{command} ...` 做 MAP 操作；不要使用 MCP、curl 或手写 httpx 调 MAP API。
- 先执行 `{command} todos` 读取最新待办；必要时再 show 对象详情。
- 只处理这一条事件直接相关的一项工作；如果已无事可做，简短说明后结束。
- approve / start / complete / execute 这类高风险动作必须遵守项目 skill 的门禁。

事件：
```json
{event_json}
```
"""


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


def _host_events(todos: dict[str, Any]) -> list[WakeEvent]:
    events: list[WakeEvent] = []
    for item in todos.get("pending_topic_replies") or []:
        topic_id = str(item.get("topic_id") or "")
        comment_id = str(item.get("comment_id") or item.get("thread_root_id") or "")
        if not topic_id or not comment_id:
            continue
        events.append(
            WakeEvent(
                persona="host",
                kind="pending_topic_reply",
                object_id=topic_id,
                fingerprint=f"host:pending_topic_reply:{topic_id}:{comment_id}",
                title=item.get("topic_title"),
                reason="reply to a pending topic thread",
                payload=_compact_payload(item, keys=("topic_id", "topic_title", "comment_id", "thread_root_id")),
            )
        )
    for item in todos.get("my_open_topics") or []:
        topic_id = str(item.get("id") or "")
        if not topic_id:
            continue
        topic_key = ":".join(
            str(item.get(key) or "")
            for key in ("discussion_round", "round_summary_count", "comment_count", "updated_at")
        ) or "open"
        events.append(
            WakeEvent(
                persona="host",
                kind="topic_lifecycle",
                object_id=topic_id,
                fingerprint=f"host:topic_lifecycle:{topic_id}:{topic_key}",
                title=item.get("title"),
                reason="check whether the hosted topic needs summary, round advance, or promotion",
                payload=_compact_payload(
                    item,
                    keys=("id", "title", "discussion_round", "round_summary_count", "comment_count", "updated_at"),
                ),
            )
        )
    for item in todos.get("my_open_experiments") or []:
        experiment_id = str(item.get("id") or "")
        if not experiment_id:
            continue
        phase = str(item.get("phase") or "open")
        version = str(item.get("current_plan_version") or "")
        open_count = str(item.get("open_unreasonable_count") or "")
        events.append(
            WakeEvent(
                persona="host",
                kind="experiment_lifecycle",
                object_id=experiment_id,
                fingerprint=f"host:experiment_lifecycle:{experiment_id}:{phase}:v{version}:u{open_count}",
                title=item.get("title"),
                reason="check whether a host-owned experiment needs lifecycle action",
                payload=_compact_payload(
                    item,
                    keys=("id", "title", "phase", "current_plan_version", "open_unreasonable_count"),
                ),
            )
        )
    return events


def _participant_events(todos: dict[str, Any], *, include_open_topics: bool) -> list[WakeEvent]:
    events: list[WakeEvent] = []
    for item in todos.get("mentions") or []:
        topic_id = str(item.get("topic_id") or "")
        source_id = str(item.get("source_id") or item.get("id") or "")
        if not topic_id or not source_id:
            continue
        events.append(
            WakeEvent(
                persona="participant",
                kind="mention",
                object_id=topic_id,
                fingerprint=f"participant:mention:{topic_id}:{source_id}",
                title=item.get("topic_title"),
                reason="reply to an @mention",
                payload=_compact_payload(item, keys=("id", "topic_id", "source_id", "author_name")),
            )
        )
    if include_open_topics:
        for item in todos.get("open_topics") or []:
            topic_id = str(item.get("id") or "")
            if not topic_id:
                continue
            events.append(
                WakeEvent(
                    persona="participant",
                    kind="open_topic_opportunity",
                    object_id=topic_id,
                    fingerprint=f"participant:open_topic:{topic_id}:{item.get('comment_count') or ''}:{item.get('updated_at') or ''}",
                    title=item.get("title"),
                    reason="inspect whether to participate in an open topic",
                    payload=_compact_payload(
                        item,
                        keys=("id", "title", "discussion_round", "round_summary_count", "comment_count", "updated_at"),
                    ),
                )
            )
    return events


def _reviewer_events(todos: dict[str, Any]) -> list[WakeEvent]:
    events: list[WakeEvent] = []
    for item in todos.get("pending_reviews") or []:
        experiment_id = str(item.get("id") or "")
        if not experiment_id:
            continue
        version = str(item.get("current_plan_version") or "")
        events.append(
            WakeEvent(
                persona="reviewer",
                kind="pending_review",
                object_id=experiment_id,
                fingerprint=f"reviewer:pending_review:{experiment_id}:v{version}",
                title=item.get("title"),
                reason="review a submitted experiment plan",
                payload=_compact_payload(item, keys=("id", "title", "phase", "current_plan_version")),
            )
        )
    for item in todos.get("pending_replies") or []:
        status = str(item.get("status") or "")
        item_id = str(item.get("item_id") or item.get("id") or "")
        if status != "addressed" or not item_id:
            continue
        events.append(
            WakeEvent(
                persona="reviewer",
                kind="addressed_review_item",
                object_id=item_id,
                fingerprint=f"reviewer:addressed_review_item:{item_id}",
                reason="resolve or re-check an addressed review item",
                payload=_compact_payload(item, keys=("item_id", "id", "status", "experiment_id")),
            )
        )
    return events


def _generic_events(persona: str, todos: dict[str, Any]) -> list[WakeEvent]:
    if any(todos.values()):
        return [
            WakeEvent(
                persona=persona,
                kind="todos_non_empty",
                object_id=persona,
                fingerprint=f"{persona}:todos_non_empty:{_stable_json(todos)}",
                reason="inspect non-empty MAP todos",
            )
        ]
    return []


def _compact_payload(item: dict[str, Any], *, keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: item[key] for key in keys if key in item and item[key] not in (None, "")}


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@APP.command()
def run(
    persona: str = typer.Option("host", "--persona", help="MAP persona to wake: host, participant, or reviewer."),
    project_root: Path = typer.Option(Path("."), "--project-root", help="Project root containing .map/."),
    map_cmd: str = typer.Option("map", "--map-cmd", help="MAP CLI command."),
    interval: float = typer.Option(30.0, "--interval", min=1.0, help="Polling interval in seconds."),
    once: bool = typer.Option(False, "--once", help="Run one cycle and exit."),
    max_cycles: int | None = typer.Option(None, "--max-cycles", min=1, help="Stop after N cycles."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print wake actions without invoking runtime."),
    max_wakes_per_cycle: int = typer.Option(3, "--max-wakes-per-cycle", min=1),
    cooldown_seconds: float = typer.Option(300.0, "--cooldown-seconds", min=0.0),
    state_file: Path | None = typer.Option(Path(".map/runtime-waker-state.json"), "--state-file"),
    backend: str = typer.Option("claude", "--backend", help="Runtime backend: claude or codex."),
    model: str | None = typer.Option(None, "--model", help="Optional runtime model override."),
    runtime_home: Path | None = typer.Option(None, "--runtime-home", help="Optional HOME for the runtime process."),
    codex_bin: str | None = typer.Option(None, "--codex-bin", help="Optional Codex binary path for --backend codex."),
    force: bool = typer.Option(False, "--force", help="Wake even if the event was already handled."),
    include_participant_open_topics: bool = typer.Option(
        False,
        "--include-participant-open-topics",
        help="Also wake participant for open-topic opportunities when todos include open_topics.",
    ),
) -> None:
    root = project_root.resolve()
    state_file_path = None
    if state_file is not None:
        state_file_path = state_file if state_file.is_absolute() else root / state_file
        state_file_path = state_file_path.resolve()
    runtime_home_path = None
    if runtime_home is not None:
        runtime_home_path = runtime_home if runtime_home.is_absolute() else root / runtime_home
        runtime_home_path = runtime_home_path.resolve()
    cfg = RuntimeWakerConfig(
        persona=persona,
        interval=interval,
        once=once,
        max_cycles=max_cycles,
        dry_run=dry_run,
        max_wakes_per_cycle=max_wakes_per_cycle,
        cooldown_seconds=cooldown_seconds,
        state_file=state_file_path,
        project_root=root,
        map_cmd=map_cmd,
        backend=backend,
        model=model,
        runtime_home=runtime_home_path,
        codex_bin=codex_bin,
        force=force,
        include_participant_open_topics=include_participant_open_topics,
    )
    client = MapCommandClient(map_cmd=map_cmd, persona=persona, project_root=root, dry_run=dry_run)
    stats = RuntimeWaker(client=client, config=cfg).run_forever()
    typer.echo(json.dumps(asdict(stats), ensure_ascii=False, indent=2))


def main() -> None:
    APP()


if __name__ == "__main__":
    main()
