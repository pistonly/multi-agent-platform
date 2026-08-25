"""MAP participant bridge — single backend: in-process Claude SDK client with session resume.

.. deprecated::
    Legacy bridge entry. Use ``./scripts/start-all-simple-wakers.sh`` (simple-waker) and
    ``map --persona participant`` with topic-participant Skill instead.
    See ``docs/LEGACY-ENTRY-MATRIX.md``. Phase 1: retained with deprecation label only.

After v0.7 P4 the participant bridge holds one ``ClaudeSDKClient`` for the
lifetime of the bridge process; ``claude_session_id`` is persisted in the
bridge state file so the next restart resumes the same Claude session via
``ClaudeAgentOptions(resume=...)``.

Claude decides what to do (which open topic to join, what to comment,
when to wait for host's Round Summary) by invoking skills from
``.cursor/skills/`` after consulting ``map --persona participant todos``
itself. The bridge only provides a brief wake-up prompt per cycle.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer
import yaml

from cli.agent_client import PersonaAgentClient, make_wakeup_prompt
from cli.bridge_state import load_bridge_state, save_bridge_state
from cli.host_worker_types import WorkerError
from cli.map_command_client import MapCommandClient
from cli.worker_cycle_log import log_cycle_summary

VALID_AGENT_BACKENDS: tuple[str, ...] = ("claude-agent",)

PARTICIPANT_CYCLE_SUMMARY_FIELDS = [
    "cycles",
    "comments_created",
    "dry_run_actions",
    "runner_invocations",
    "runner_skips",
    "runner_errors",
    "opportunities_seen",
]


class ParticipantMapClient(MapCommandClient):
    def topic_list_open(self) -> list[dict[str, Any]]:
        rows = self._run(["topic", "list", "--status", "open"])
        if rows is None:
            return []
        if isinstance(rows, list):
            return rows
        return []


@dataclass
class ParticipantWorkerConfig:
    interval: float = 30.0
    once: bool = False
    max_cycles: int | None = None
    dry_run: bool = False
    state_file: Path | None = Path(".map/participant-bridge-state.json")
    agent_backend: str = "claude-agent"


@dataclass
class ParticipantWorkerStats:
    cycles: int = 0
    comments_created: int = 0
    dry_run_actions: int = 0
    runner_invocations: int = 0
    runner_skips: int = 0
    runner_errors: int = 0
    opportunities_seen: int = 0


@dataclass
class ParticipantWorker:
    client: ParticipantMapClient
    config: ParticipantWorkerConfig = field(default_factory=ParticipantWorkerConfig)
    agent_id: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    _state_dirty: bool = False
    _injected_agent_client: PersonaAgentClient | None = None

    def __init__(
        self,
        client: ParticipantMapClient,
        config: ParticipantWorkerConfig | None = None,
        *,
        agent_client: PersonaAgentClient | None = None,
    ) -> None:
        self.client = client
        self.config = config or ParticipantWorkerConfig()
        if self.config.agent_backend not in VALID_AGENT_BACKENDS:
            raise WorkerError(
                f"Unknown agent_backend={self.config.agent_backend!r}; expected one of {VALID_AGENT_BACKENDS}"
            )
        self.agent_id = None
        self.state = _load_state(self.config.state_file)
        self._state_dirty = False
        self._injected_agent_client = agent_client

    def run_forever(self) -> ParticipantWorkerStats:
        return asyncio.run(self._run_forever_async())

    async def _run_forever_async(self) -> ParticipantWorkerStats:
        agent_client = await self._ensure_agent_client()
        try:
            total = ParticipantWorkerStats()
            while True:
                stats = await self._run_once_claude_agent(agent_client)
                total.cycles += stats.cycles
                total.comments_created += stats.comments_created
                total.dry_run_actions += stats.dry_run_actions
                total.runner_invocations += stats.runner_invocations
                total.runner_skips += stats.runner_skips
                total.runner_errors += stats.runner_errors
                total.opportunities_seen += stats.opportunities_seen
                log_cycle_summary("participant", total, fields=PARTICIPANT_CYCLE_SUMMARY_FIELDS)

                if self.config.once:
                    break
                if self.config.max_cycles is not None and total.cycles >= self.config.max_cycles:
                    break
                await asyncio.sleep(self.config.interval)
            return total
        finally:
            await agent_client.disconnect()

    async def _ensure_agent_client(self) -> PersonaAgentClient:
        client = self._injected_agent_client
        if client is None:
            client = PersonaAgentClient(
                persona="participant",
                state=self.state,
                save_state_fn=lambda: self._save_state_if_needed(force=True),
                project_root=self._git_repo() or Path.cwd(),
            )
            self._injected_agent_client = client
        await client.connect()
        return client

    async def _run_once_claude_agent(
        self, agent_client: PersonaAgentClient
    ) -> ParticipantWorkerStats:
        """One polling cycle: brief wake-up, agent handles all actions itself."""
        self._ensure_identity()
        stats = ParticipantWorkerStats(cycles=1)
        todos = self.client.todos() or {}
        prompt = make_wakeup_prompt("participant", todos)
        on_event = lambda event: self._log_agent_event(event)  # noqa: E731
        try:
            status = await agent_client.wake_up(prompt, on_event=on_event)
        except Exception as exc:  # noqa: BLE001
            self._log_json({"action": "agent_wakeup_error"}, error=str(exc))
            stats.runner_errors += 1
            return stats

        self._log_json(
            {"action": "agent_wakeup_done"},
            status=status,
            session_id=agent_client.state.get("claude_session_id"),
        )
        stats.runner_invocations += 1
        return stats

    def _log_agent_event(self, event: dict[str, Any]) -> None:
        self._log_json({"action": "agent_event"}, **event)

    def _ensure_identity(self) -> None:
        if self.agent_id is not None:
            return
        me = self.client.whoami()
        if not me or not me.get("id"):
            raise WorkerError(
                "Could not resolve participant identity; run `map --persona participant persona whoami`"
            )
        self.agent_id = str(me["id"])

    def _save_state_if_needed(self, *, force: bool = False) -> None:
        if self.config.dry_run:
            return
        if not force and not self._state_dirty:
            return
        _save_state(self.config.state_file, self.state)
        self._state_dirty = False

    def _log_json(self, base: dict[str, Any], **fields: Any) -> None:
        event = {
            **base,
            "dry_run": self.config.dry_run,
            **{key: value for key, value in fields.items() if value not in (None, "")},
        }
        typer.echo(json.dumps(event, ensure_ascii=True, sort_keys=True), err=True)

    def _git_repo(self) -> Path | None:
        if isinstance(self.client, MapCommandClient) and self.client.project_root is not None:
            return self.client.project_root
        return Path.cwd()


def _load_state(path: Path | None) -> dict[str, Any]:
    return load_bridge_state(path, bridge_name="participant", default_collections=("topics",))


def _save_state(path: Path | None, state: dict[str, Any]) -> None:
    save_bridge_state(path, state)


def run(
    persona: str = typer.Option("participant", "--persona", "-p"),
    project_root: Path | None = typer.Option(None, "--project-root"),
    map_cmd: str = typer.Option("map", "--map-cmd"),
    interval: float = typer.Option(30.0, "--interval", min=1.0),
    once: bool = typer.Option(False, "--once"),
    max_cycles: int | None = typer.Option(None, "--max-cycles", min=1),
    dry_run: bool = typer.Option(False, "--dry-run"),
    state_file: Path | None = typer.Option(
        Path(".map/participant-bridge-state.json"),
        "--state-file",
    ),
    agent_backend: str = typer.Option(
        "claude-agent",
        "--agent-backend",
        help="Agent backend. Only 'claude-agent' is supported.",
    ),
) -> None:
    config = ParticipantWorkerConfig(
        interval=interval,
        once=once,
        max_cycles=max_cycles,
        dry_run=dry_run,
        state_file=state_file,
        agent_backend=agent_backend,
    )
    client = ParticipantMapClient(
        map_cmd=map_cmd,
        persona=persona,
        project_root=project_root,
        dry_run=dry_run,
    )
    stats = ParticipantWorker(client, config).run_forever()
    typer.echo(yaml.safe_dump(stats.__dict__, allow_unicode=True, sort_keys=False))


def main() -> None:
    typer.run(run)


if __name__ == "__main__":
    main()
