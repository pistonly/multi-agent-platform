from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

import typer

from cli.agent_client import PersonaAgentClient
from cli.bridge_state import load_bridge_state, save_bridge_state
from cli.host_worker_types import WorkerError
from cli.map_command_client import MapCommandClient
from cli.worker_cycle_log import log_cycle_summary

APP = typer.Typer(add_completion=False)


# ---------------------------------------------------------------------------
# action_item escalation (experiment B, plan §3 / I4)
# ---------------------------------------------------------------------------
#
# The waker drives the three-stage escalation timeline
# ``T+24h → T+72h → 7d × N → stale`` against each open action_item whose
# owner matches the current persona. ``should_wake_action_item`` is the
# pure decision function — kept side-effect-free so unit tests can stamp
# ``now`` directly without touching the DB. ``scan_pending_action_items``
# is the thin caller-friendly wrapper used by ``_run_once_async``.
#
# All threshold numbers mirror ``server.services.action_item_service``
# (``WAKE_STAGE_THRESHOLDS`` / ``WAKE_REPEAT_INTERVAL_DAYS`` /
# ``WAKE_MAX_COUNT_BEFORE_STALE``). Keep these two definitions in sync —
# the I3 unit test ``test_threshold_constants_match_plan_section_three``
# is the canonical lint and we re-export the server-side constants here
# for runtime use.
# ---------------------------------------------------------------------------


class ActionItemWakeDecision(str, Enum):
    """Outcome of ``should_wake_action_item`` for one action_item at ``now``."""

    WAKE = "wake"
    STALE = "stale"
    SKIP = "skip"


# Mirrored from server.services.action_item_service. Importing would force
# the waker CLI to depend on the server-side stack (SQLAlchemy models +
# FastAPI deps), which violates the waker's no-FastAPI invariant. Kept
# short and obvious so a reviewer can spot drift.
_WAKE_STAGE_HOURS: tuple[tuple[int, int], ...] = (
    (1, 24),   # wake_count_after_increment=1 → first wake at T+24h
    (2, 72),   # wake_count_after_increment=2 → second wake at T+72h
)
_WAKE_REPEAT_DAYS = 7  # after the 72h wake, fire every 7d
_WAKE_MAX_BEFORE_STALE = 4  # 4th unanswered wake → stale


def _parse_iso_datetime(value: Any) -> datetime | None:
    """Parse an ISO-8601 string into an aware UTC datetime.

    Defensive about the variety of shapes the SDK / API may emit
    (``...Z`` vs ``...+00:00`` vs naive ISO). Returns ``None`` if the value
    is missing or unparseable — callers treat ``None`` as "skip".
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def should_wake_action_item(
    item: dict[str, Any],
    *,
    now: datetime | None = None,
) -> ActionItemWakeDecision:
    """Decide the escalation action for one open action_item at ``now``.

    Pure function (no I/O, no clock). Returns ``WAKE`` when the waker
    should bump ``wake_count`` and emit a wake event, ``STALE`` when the
    4th wake has already fired and the assignee still hasn't responded
    (we mark stale + stop waking), or ``SKIP`` when the escalation
    timeline does not yet call for an action.

    Rules (plan §3, mirrored from ``server.services.action_item_service``):

    - Only open items are eligible; closed items (done / cancelled) and
      unassigned items (no ``owner_agent_id``) are skipped.
    - Stale items (``stale_at`` set) are skipped forever — once we've
      written the diagnostic audit row we stop bothering the assignee.
    - First wake fires 24h after ``first_open_at``; second at 72h; further
      wakes every 7d; the (N+1)th check after the 4th wake writes stale.
    - ``now`` defaults to ``datetime.now(UTC)`` — callers that need a
      deterministic clock (tests, dogfood) inject their own.
    """
    if not isinstance(item, dict):
        return ActionItemWakeDecision.SKIP
    if item.get("status") != "open":
        return ActionItemWakeDecision.SKIP
    if not item.get("owner_agent_id"):
        # plan §3 「仅 assignee」 — unassigned items must not be woken by
        # anyone, the waker has no addressee.
        return ActionItemWakeDecision.SKIP
    if _parse_iso_datetime(item.get("stale_at")) is not None:
        return ActionItemWakeDecision.SKIP

    current = now or datetime.now(UTC)
    first_open_at = _parse_iso_datetime(item.get("first_open_at"))
    if first_open_at is None:
        # Pre-I1 backfill may have missed this row (closed before I1, etc).
        # Conservatively skip — re-running the backfill is the remediation.
        return ActionItemWakeDecision.SKIP
    last_woken_at = _parse_iso_datetime(item.get("last_woken_at"))
    wake_count = int(item.get("wake_count") or 0)

    elapsed = current - first_open_at

    # Stage 1 & 2: hard thresholds keyed to first_open_at.
    for target_count, min_hours in _WAKE_STAGE_HOURS:
        if wake_count + 1 == target_count and elapsed >= timedelta(hours=min_hours):
            return ActionItemWakeDecision.WAKE

    # Stages 3+: every 7d after the last wake.
    if 2 <= wake_count < _WAKE_MAX_BEFORE_STALE and last_woken_at is not None:
        if current - last_woken_at >= timedelta(days=_WAKE_REPEAT_DAYS):
            return ActionItemWakeDecision.WAKE

    # Past the 4th unanswered wake: write stale.
    if wake_count >= _WAKE_MAX_BEFORE_STALE and last_woken_at is not None:
        if current - last_woken_at >= timedelta(days=_WAKE_REPEAT_DAYS):
            return ActionItemWakeDecision.STALE

    return ActionItemWakeDecision.SKIP


def scan_pending_action_items(
    action_items: list[dict[str, Any]],
    *,
    persona_agent_id: str | None,
    now: datetime | None = None,
) -> list[tuple[str, ActionItemWakeDecision]]:
    """Filter the ``action_items`` todo payload through ``should_wake_action_item``.

    Returns a list of ``(action_item_id, decision)`` pairs. Only items
    owned by ``persona_agent_id`` are considered — the waker must never
    wake another agent's action_item even if it shows up in the
    cross-project listing. Items whose owner doesn't match are skipped
    silently (not raised) — the per-persona todos feed already scopes
    this but we re-check defensively against potential payload leakage.
    """
    decisions: list[tuple[str, ActionItemWakeDecision]] = []
    for item in action_items or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        if persona_agent_id and str(item.get("owner_agent_id") or "") != str(persona_agent_id):
            continue
        decisions.append((item_id, should_wake_action_item(item, now=now)))
    return decisions

# Codex wake: inject dispatcher + shared collab + persona skills (deduped, file order).
WAKE_SKILL_CHAIN: dict[str, tuple[str, ...]] = {
    "host": ("map-runtime-waker", "map-project-collab", "topic-host", "experiment-host"),
    "participant": ("map-runtime-waker", "map-project-collab", "topic-participant"),
    "reviewer": ("map-runtime-waker", "map-project-collab", "experiment-reviewer"),
}

# ---------------------------------------------------------------------------
# Phase 2 D1/D3/D4: SSE primary path + replay/rate-limit helpers.
# ---------------------------------------------------------------------------
#
# Three sources feed _wake_event. The fingerprint invariant is preserved across
# them: ``event_source`` only changes the sessions jsonl ``event_source`` field
# and the D4 client-side skip path; the inbound_event.UNIQUE server gate still
# sees the same fingerprint and rejects cross-source replays.
# ---------------------------------------------------------------------------

WAKE_SOURCE_POLLING = "polling"  # periodic todos/notifications poll
WAKE_SOURCE_SSE = "sse"          # real-time SSE long-poll frame
WAKE_SOURCE_REPLAY = "replay"    # SSE reconnect backfill via unread_only=true


# Notification.event -> WakeEvent.kind routing table (Phase 2 D2 §I2).
# The ``payload.kind`` enrichment done by ``emit_kind`` (server side) takes
# priority — we read it from the notifications_unread payload_json. The
# event-name fallback covers events emitted by the legacy ``emit()`` path
# (still in use for ``topic.advance_round`` / ``experiment.phase_changed`` /
# ``plan.revised`` / ``comment.created`` etc.).
_KIND_FROM_PAYLOAD_KIND: dict[str, str] = {
    "topic.lifecycle": "topic_lifecycle",
    "topic.advance_round": "topic_lifecycle",
    "topic.resolved": "topic_lifecycle",
    "topic.comment": "topic_lifecycle",
    "experiment.lifecycle": "experiment_lifecycle",
    "experiment.phase_changed": "experiment_lifecycle",
    "plan.revised": "experiment_lifecycle",
    "review.submitted": "pending_review",
    "review_item": "pending_review",
    "review_item.status_changed": "pending_replies",
    "comment.created": "pending_result_review",
}


def _notification_event_to_wake_kind(
    event: str, payload: dict[str, Any] | None
) -> str:
    """Derive a WakeEvent.kind bucket from a Notification event + payload.

    Priority: payload.kind (set by server emit_kind) > event-name pattern.
    Unknown events fall back to ``"notification"`` (generic bucket that the
    discover_wake_events path already handles via ``unread notifications``).
    """
    if isinstance(payload, dict):
        explicit = payload.get("kind")
        if isinstance(explicit, str) and explicit in _KIND_FROM_PAYLOAD_KIND:
            return _KIND_FROM_PAYLOAD_KIND[explicit]
    if event.startswith("topic.") or event.startswith("comment.topic") :
        return "topic_lifecycle"
    if (
        event.startswith("experiment.")
        or event.startswith("plan.")
        or event.startswith("comment.experiment")
    ):
        return "experiment_lifecycle"
    if event.startswith("review."):
        return "pending_review"
    if event.startswith("comment."):
        return "pending_result_review"
    if event == "addressed_review_item.status_changed":
        return "pending_replies"
    return "notification"


def parse_sse_frame(buffer: str) -> tuple[dict[str, str] | None, str]:
    """Parse one SSE frame out of ``buffer``.

    Returns ``(frame, leftover)``. Frame is a dict like ``{"event": "...", "data": "..."}``
    when the buffer contains a complete blank-line-terminated frame, otherwise
    ``None``. Comment lines (``: heartbeat``) and unknown fields are ignored.
    Multiple ``data:`` lines are joined with ``\n`` per the SSE spec.
    """
    if not buffer:
        return None, ""
    delimiter = buffer.find("\n\n")
    if delimiter == -1:
        # Some servers send \r\n\r\n; normalize then check.
        normalized = buffer.replace("\r\n", "\n")
        delimiter = normalized.find("\n\n")
        if delimiter == -1:
            return None, buffer
        frame_block = normalized[:delimiter]
        leftover = normalized[delimiter + 2 :]
    else:
        frame_block = buffer[:delimiter]
        leftover = buffer[delimiter + 2 :]
    fields: dict[str, list[str]] = {}
    for raw_line in frame_block.splitlines():
        if not raw_line or raw_line.startswith(":"):
            continue
        if ":" not in raw_line:
            # Bare field name with no value; per SSE spec treat as empty.
            name = raw_line.strip()
            value = ""
        else:
            name, _, value = raw_line.partition(":")
            # Spec: strip a single leading space.
            if value.startswith(" "):
                value = value[1:]
        fields.setdefault(name.strip(), []).append(value)
    if not fields:
        return None, leftover
    return (
        {key: "\n".join(values) for key, values in fields.items()},
        leftover,
    )


def sse_backoff_delay(
    consecutive_failures: int,
    *,
    base: float,
    cap: float,
) -> float:
    """Compute D3 exponential backoff delay: 1s, 2s, 4s, ..., capped.

    Pure function (no clock, no I/O) so unit tests can pin the sequence.
    """
    if consecutive_failures <= 0:
        return 0.0
    delay = base * (2 ** (consecutive_failures - 1))
    return min(delay, cap)


def _resolve_api_url(project_root: Path) -> str | None:
    """Read ``api_url`` from ``.map/config.yaml``. Returns None if missing."""
    config_path = project_root / ".map" / "config.yaml"
    if not config_path.is_file():
        return None
    try:
        import yaml  # local import: top-of-file import already pulls yaml via map_command_client
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    api_url = data.get("api_url")
    if not isinstance(api_url, str) or not api_url.strip():
        return None
    return api_url.rstrip("/")


def _resolve_bearer_token(project_root: Path, persona: str) -> str | None:
    """Read the bearer token for ``persona`` from ``.map/agents.local.yaml``."""
    agents_path = project_root / ".map" / "agents.local.yaml"
    if not agents_path.is_file():
        return None
    try:
        import yaml
        data = yaml.safe_load(agents_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    personas = data.get("personas") if isinstance(data, dict) else None
    if not isinstance(personas, dict):
        return None
    entry = personas.get(persona) or {}
    if not isinstance(entry, dict):
        return None
    token = entry.get("token")
    return str(token) if isinstance(token, str) and token else None

# Stable namespace used to derive a UUID from a (agent_id, fingerprint) pair when
# the upstream WakeEvent has no notification UUID of its own. UUID5 is deterministic
# so the same fingerprint always maps to the same inbound_event.event_id, which
# keeps DB rows joinable across cycles.
_EVENT_UUID_NAMESPACE = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _event_uuid_for_fingerprint(agent_id: str, fingerprint: str) -> uuid.UUID:
    """Derive a stable event_id UUID for an inbound_event record."""
    return uuid.uuid5(_EVENT_UUID_NAMESPACE, f"{agent_id}:{fingerprint}")


def _is_legacy_v1_fingerprint(fingerprint: str) -> bool:
    """Return True for pre-v0.9 ``inbound:<event_id>`` fingerprints.

    Mirrors :func:`server.services.notification_service.is_legacy_v1_fingerprint`
    so the runtime-waker can short-circuit BEFORE paying for the resume
    pipeline. The server's D6 gate still treats it via the
    ``InboundEvent.rejection_count`` path (see M30A/M31 I2) — this is purely a
    client-side early-exit.

    We deliberately duplicate the ``startswith("inbound:")`` rule instead of
    importing :mod:`server.services.notification_service` to avoid pulling the
    full server module graph into the CLI runtime. v0.9 fingerprints are
    namespace-prefixed (``{persona}:notification:...`` or
    ``{persona}:{todo_bucket}:...``) so they never start with ``inbound:``.
    """
    return fingerprint.startswith("inbound:")


def wake_skill_paths(project_root: Path, persona: str) -> list[Path]:
    """Skill files to inject on Codex wake (missing files are skipped)."""
    chain = WAKE_SKILL_CHAIN.get(persona, ("map-runtime-waker", "map-project-collab"))
    skills_root = project_root / ".cursor" / "skills"
    paths: list[Path] = []
    seen: set[str] = set()
    for name in chain:
        if name in seen:
            continue
        seen.add(name)
        path = skills_root / name / "SKILL.md"
        if path.is_file():
            paths.append(path)
    return paths


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
    # How long a "woken" event stays deduped before self-healing re-evaluation.
    # Unlike cooldown_seconds (retry backoff), this bounds how long an agent that
    # woke but took no action can stay stuck before the waker reconsiders it.
    woken_cooldown_seconds: float = 1800.0
    # Heartbeat interval for woken/server_skip re-wake when todos still show pending
    # work. When set (or via start script defaulting to poll interval), the waker
    # re-resumes the agent after this many seconds even if D6 returns 409.
    # When None, falls back to woken_cooldown_seconds (backward compatible).
    heartbeat_seconds: float | None = None
    # Persona-level single-flight: after a successful wake, skip ALL of this
    # persona's events until the window elapses, so a background session still
    # executing a prior wake is not preempted by another event waking into a
    # different context (e.g. experiment A still running when experiment B is
    # selected next cycle). 0 disables -> pure per-event dedup.
    persona_inflight_seconds: float = 1800.0
    # TTL sweep: drop event dedup entries that can no longer affect _should_skip_event
    # (orphaned by a derived-fingerprint change). Default on; --no-prune-events disables.
    prune_events: bool = True
    state_file: Path | None = Path(".map/runtime-waker-state.json")
    project_root: Path = Path.cwd()
    map_cmd: str = "map"
    backend: str = "claude"
    model: str | None = None
    runtime_home: Path | None = None
    codex_bin: str | None = None
    force: bool = False
    # Phase 2 D1: enable SSE primary path. When on, the waker runs an SSE
    # long-poll alongside the polling cycle and wakes from incoming frames
    # before the polling interval would have re-discovered them. Polling
    # remains the last-line fallback (D6) — disable only for offline tests.
    sse_enabled: bool = True
    # D4 client-side rate limit: skip wake when same fingerprint was woken
    # within this window. The server-side UNIQUE gate is unchanged (still
    # authoritative across processes); this is a cheap in-memory gate to
    # avoid round-trips during SSE replay storms. Replay events bypass.
    sse_recent_resume_window_seconds: float = 60.0
    # D3 exponential backoff: 1s, 2s, 4s, 8s, 16s, capped at max.
    sse_backoff_base_seconds: float = 1.0
    sse_backoff_max_seconds: float = 30.0
    # Bound per-connection read timeout so dead connections don't hang forever.
    sse_read_timeout_seconds: float = 90.0
    # Bound how many unread wakeable notifications we replay on (re)connect.
    sse_replay_limit: int = 200
    # Bound the SSE handshake connect timeout separately from per-read timeout.
    sse_connect_timeout_seconds: float = 10.0


@dataclass
class RuntimeWakerStats:
    cycles: int = 0
    events_seen: int = 0
    wakes_sent: int = 0
    wake_skips: int = 0
    wake_errors: int = 0
    dry_run_actions: int = 0
    # Orphaned event dedup entries dropped by the TTL sweep (see _prune_events).
    # Mirrors dry_run_actions: aggregated across cycles so run_forever totals stay correct.
    events_pruned: int = 0
    # Action-item escalation counters (experiment B, plan §3 / I4). Mirrors
    # the wake / skip / error split so dogfood dashboards can read
    # "wake:stale:skip" ratios from ``log_cycle_summary`` output.
    action_items_wake: int = 0
    action_items_stale: int = 0
    action_items_decision_skip: int = 0
    action_items_decision_errors: int = 0
    # Phase 2 D1/D3/D4 SSE stats. Aggregated across cycles so run_forever
    # totals stay correct. Source counters (sse / replay / polling) let the
    # reviewer trace which path triggered each wake in the A1a/A1b/A3 reports.
    sse_connect_attempts: int = 0
    sse_connect_successes: int = 0
    sse_events_received: int = 0
    sse_wakes_sent: int = 0
    sse_replay_wakes_sent: int = 0
    sse_replay_runs: int = 0
    sse_rate_limit_skips: int = 0
    sse_disconnects: int = 0
    sse_backoff_seconds_total: float = 0.0
    sse_last_disconnect_reason: str | None = None
    # Phase 2 I5-A3: empty-polling accounting. ``polling_cycles_empty`` counts
    # steady-state cycles where ``events_seen == 0`` (the SSE long-poll is the
    # primary path; the polling 兜底 finds nothing to wake).
    # ``polling_cycles_recovery_excluded`` counts cycles that fell inside an
    # SSE reconnect window — these are excluded from the empty-ratio
    # denominator per plan §A3 (双档受控流量 + 边界分段).
    polling_cycles_empty: int = 0
    polling_cycles_recovery_excluded: int = 0

    def add(self, other: "RuntimeWakerStats") -> None:
        self.cycles += other.cycles
        self.events_seen += other.events_seen
        self.wakes_sent += other.wakes_sent
        self.wake_skips += other.wake_skips
        self.wake_errors += other.wake_errors
        self.dry_run_actions += other.dry_run_actions
        self.events_pruned += other.events_pruned
        self.action_items_wake += other.action_items_wake
        self.action_items_stale += other.action_items_stale
        self.action_items_decision_skip += other.action_items_decision_skip
        self.action_items_decision_errors += other.action_items_decision_errors
        self.sse_connect_attempts += other.sse_connect_attempts
        self.sse_connect_successes += other.sse_connect_successes
        self.sse_events_received += other.sse_events_received
        self.sse_wakes_sent += other.sse_wakes_sent
        self.sse_replay_wakes_sent += other.sse_replay_wakes_sent
        self.sse_replay_runs += other.sse_replay_runs
        self.sse_rate_limit_skips += other.sse_rate_limit_skips
        self.sse_disconnects += other.sse_disconnects
        self.sse_backoff_seconds_total += other.sse_backoff_seconds_total
        self.sse_last_disconnect_reason = other.sse_last_disconnect_reason or self.sse_last_disconnect_reason
        # Phase 2 I5-A3: aggregate empty/recovery-excluded cycle counters so
        # run_forever totals survive across iterations.
        self.polling_cycles_empty += other.polling_cycles_empty
        self.polling_cycles_recovery_excluded += other.polling_cycles_recovery_excluded


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

        skill_inputs = [
            SkillInput(name=path.parent.name, path=str(path))
            for path in wake_skill_paths(self.project_root, persona)
        ]
        input_items: list[Any] = skill_inputs + [TextInput(prompt)]

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


_CURSOR_EXPORT_RE = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_CURSOR_CREDENTIAL_ENV_KEYS: tuple[str, ...] = ("CURSOR_API_KEY",)
_CURSOR_MODEL_ENV_KEYS: tuple[str, ...] = ("CURSOR_MODEL",)


class CursorSdkWakeBackend:
    """Per-wake Cursor SDK backend: create or resume a local agent by agent_id."""

    def __init__(
        self,
        *,
        project_root: Path,
        model: str | None = None,
    ) -> None:
        self.project_root = project_root
        self.model = model

    def wake(
        self,
        *,
        persona: str,
        prompt: str,
        session_id: str | None,
    ) -> WakeResult:
        del persona
        try:
            from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions
        except ImportError as exc:  # pragma: no cover
            raise WorkerError("Install cursor-sdk to use the cursor runtime backend") from exc

        api_key = self._resolve_api_key()
        if not api_key:
            raise WorkerError(
                "CURSOR_API_KEY not found in environment or ~/.bashrc / ~/.profile / ~/.bash_profile"
            )

        model = self.model or self._resolve_model() or "composer-2.5"
        options = AgentOptions(
            api_key=api_key,
            model=model,
            local=LocalAgentOptions(
                cwd=str(self.project_root),
                setting_sources=["project"],
            ),
        )

        try:
            if session_id:
                agent_cm = Agent.resume(session_id, options)
            else:
                agent_cm = Agent.create(options)
            with agent_cm as agent:
                run = agent.send(prompt)
                result = run.wait()
                agent_id = agent.agent_id
        except CursorAgentError as exc:
            raise WorkerError(f"Cursor wake failed: {exc}") from exc

        if result.status == "error":
            raise WorkerError(f"Cursor run failed: {getattr(result, 'id', 'unknown')}")

        return WakeResult(session_id=agent_id, response_text=result.result or "")

    def _resolve_api_key(self) -> str | None:
        return self._resolve_env_key("CURSOR_API_KEY")

    def _resolve_model(self) -> str | None:
        for key in _CURSOR_MODEL_ENV_KEYS:
            value = self._resolve_env_key(key)
            if value:
                return value
        return None

    def _resolve_env_key(self, name: str) -> str | None:
        existing = os.environ.get(name, "").strip()
        if existing:
            return existing
        home = Path.home()
        for path in (
            self.project_root / ".map" / ".cursor-env",
            home / ".bashrc",
            home / ".profile",
            home / ".bash_profile",
        ):
            found = self._read_export(path, name)
            if found:
                return found
        return None

    @staticmethod
    def _read_export(path: Path, name: str) -> str | None:
        if not path.is_file():
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        for line in text.splitlines():
            match = _CURSOR_EXPORT_RE.match(line)
            if match and match.group(1) == name:
                value = match.group(2).strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                elif value and value[0] not in {"'", '"'}:
                    value = re.sub(r"\s+#.*$", "", value).strip()
                if value:
                    return value
        return None


def wake_context_key(event: WakeEvent) -> str:
    """Return the MAP object context that owns a resumed runtime session."""
    payload = event.payload or {}
    bucket = event.kind
    if bucket in {
        "pending_reviews",
        "pending_result_reviews",
        "pending_replies",
        "my_open_experiments",
    }:
        return f"experiment:{event.object_id}"
    if bucket == "notification":
        target_type = str(payload.get("target_type") or "")
        if target_type == "experiment" and payload.get("target_id"):
            return f"experiment:{payload['target_id']}"
        if target_type == "topic" and payload.get("target_id"):
            return f"topic:{payload['target_id']}"
        return f"notification:{event.object_id}"
    if bucket == "mentions" and payload.get("experiment_id"):
        return f"experiment:{payload['experiment_id']}"
    if bucket in {
        "mentions",
        "pending_topic_replies",
        "pending_advance_rounds",
        "pending_round_acks",
        "my_open_topics",
    }:
        return f"topic:{event.object_id}"
    return f"{bucket}:{event.object_id}"


def should_reset_session_for_context(
    *,
    last_wake_context_key: str | None,
    wake_context_key: str,
) -> bool:
    """Return True when the wake context changed and we must not resume the prior session."""
    if last_wake_context_key is None:
        return False
    return str(last_wake_context_key) != wake_context_key


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
        # Process start time: persona inflight must only count wakes that
        # happened during THIS process. ``last_woken_at`` persists in the state
        # file, so without this guard a restart inherits the previous process's
        # wake and suppresses the new process for the whole inflight window
        # (observed: host/reviewer idle for 30min after every waker restart).
        self._started_at: datetime = datetime.now(UTC)
        # Phase 2 D4: in-process fingerprint → last resume attempt timestamp.
        # Bounded by sse_recent_resume_window_seconds; pruned in
        # _run_once_async + after each SSE replay. Survives only this process.
        self._recent_resume_attempts: dict[str, datetime] = {}
        # Phase 2 I5-A3: SSE recovery window marker. Set by the SSE loop while
        # it is reconnecting + replaying missed events (between
        # ``sse_disconnects += 1`` and the next ``_sse_replay_unread`` return),
        # read by ``_run_once_async`` to mark polling cycles as
        # ``recovery_excluded`` so they don't penalize the empty-polling ratio.
        self._sse_recovery_in_progress: bool = False
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
                    "events_pruned",
                    "action_items_wake",
                    "action_items_stale",
                    "action_items_decision_skip",
                    "action_items_decision_errors",
                    "sse_connect_attempts",
                    "sse_connect_successes",
                    "sse_events_received",
                    "sse_wakes_sent",
                    "sse_replay_wakes_sent",
                    "sse_replay_runs",
                    "sse_rate_limit_skips",
                    "sse_disconnects",
                    "sse_backoff_seconds_total",
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
        stop = asyncio.Event()
        sse_stats = RuntimeWakerStats()
        sse_task: asyncio.Task[None] | None = None
        if self.config.sse_enabled:
            sse_task = asyncio.create_task(
                self._run_sse_loop_async(stop=stop, stats=sse_stats),
                name=f"sse-{self.config.persona}",
            )
        try:
            total = RuntimeWakerStats()
            while True:
                stats = await self._run_once_async()
                total.add(stats)
                # Pull SSE stats accumulated between polling cycles.
                total.add(sse_stats)
                # Reset the shared sse_stats accumulator; we own its lifecycle.
                sse_stats = RuntimeWakerStats()
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
                        "events_pruned",
                        "action_items_wake",
                        "action_items_stale",
                        "action_items_decision_skip",
                        "action_items_decision_errors",
                        "sse_connect_attempts",
                        "sse_connect_successes",
                        "sse_events_received",
                        "sse_wakes_sent",
                        "sse_replay_wakes_sent",
                        "sse_replay_runs",
                        "sse_rate_limit_skips",
                        "sse_disconnects",
                        "sse_backoff_seconds_total",
                    ],
                )
                if self.config.once:
                    break
                if self.config.max_cycles is not None and total.cycles >= self.config.max_cycles:
                    break
                await asyncio.sleep(self.config.interval)
            return total
        finally:
            stop.set()
            if sse_task is not None:
                sse_task.cancel()
                try:
                    await sse_task
                except (asyncio.CancelledError, Exception):
                    pass
            await self.backend.disconnect()

    def run_once(self) -> RuntimeWakerStats:
        return asyncio.run(self._run_once_async())

    async def _run_once_async(self) -> RuntimeWakerStats:
        self._ensure_identity()
        stats = RuntimeWakerStats(cycles=1)
        # Phase 2 I5-A3: if SSE is in its reconnect+replay window, mark this
        # polling cycle as ``recovery_excluded`` (it ran as the fallback while
        # the primary path was down). The cycle still completes normally —
        # just not counted in the empty-ratio denominator.
        if self._sse_recovery_in_progress:
            stats.polling_cycles_recovery_excluded = 1
        todos = self.client.todos() or {}
        notifications = self.client.notifications_unread()
        events = discover_wake_events(
            self.config.persona,
            todos,
            notifications=notifications,
        )
        stats.events_seen = len(events)
        # Phase 2 I5-A3: count empty steady-state cycles. SSE recovery cycles
        # are tagged separately above and not double-counted here.
        if not self._sse_recovery_in_progress and len(events) == 0:
            stats.polling_cycles_empty = 1

        # Filter out skipped events first so a skip never consumes a wake slot,
        # then round-robin across event kinds. Without the round-robin, a long
        # backlog of one kind (e.g. hosted_topic for several open topics) would
        # monopolize the per-cycle wake budget and starve another kind (e.g.
        # experiment_lifecycle), so host could never approve/start experiments
        # while topics keep it busy.
        candidates: list[WakeEvent] = []
        for event in events:
            if not self.config.force and self._should_skip_event(event):
                stats.wake_skips += 1
                continue
            candidates.append(event)

        # Experiment B (plan §3 / I4): apply the action_item escalation
        # decision BEFORE the round-robin selection. Items the waker has
        # decided to mark stale are processed (mark-stale CLI call) and
        # then dropped from the wake candidates so they don't consume a
        # round-robin slot. Items decided wake have their state pre-mutated
        # (``mark-wake-sent``) so the agent prompt reflects the new count.
        pre_decided: list[WakeEvent] = []
        for event in candidates:
            if event.kind != "action_items":
                pre_decided.append(event)
                continue
            try:
                decision = should_wake_action_item(event.payload or {})
            except Exception as exc:  # pragma: no cover - defensive
                stats.action_items_decision_errors += 1
                self._mark_event(event, status="error", error=f"decision: {exc!r}")
                continue
            if decision == ActionItemWakeDecision.SKIP:
                stats.action_items_decision_skip += 1
                stats.wake_skips += 1
                continue
            if decision == ActionItemWakeDecision.STALE:
                if self.config.dry_run:
                    typer.echo(
                        f"[dry-run] would mark-stale action_item={event.object_id}"
                    )
                    stats.dry_run_actions += 1
                else:
                    try:
                        self.client.action_mark_stale(event.object_id)
                    except WorkerError as exc:
                        stats.action_items_decision_errors += 1
                        self._mark_event(event, status="error", error=f"mark-stale: {exc!r}")
                        continue
                stats.action_items_stale += 1
                stats.wake_skips += 1
                # Don't emit a wake prompt for the assignee after stale —
                # the diagnostic audit row + (future) admin notification
                # are the post-stale signals (plan §3 §4 + §5).
                continue
            # WAKE — pre-mutate state so the agent sees the bumped count.
            if self.config.dry_run:
                typer.echo(
                    f"[dry-run] would mark-wake-sent action_item={event.object_id}"
                )
                stats.dry_run_actions += 1
            else:
                try:
                    self.client.action_mark_wake_sent(event.object_id)
                except WorkerError as exc:
                    stats.action_items_decision_errors += 1
                    self._mark_event(event, status="error", error=f"mark-wake-sent: {exc!r}")
                    continue
            stats.action_items_wake += 1
            pre_decided.append(event)

        selected = _round_robin_by_kind(pre_decided, self.config.max_wakes_per_cycle)

        for event in selected:
            if self.config.dry_run:
                typer.echo(f"[dry-run] would wake persona={event.persona} event={event.fingerprint}")
                stats.dry_run_actions += 1
                continue
            try:
                await self._wake_event(event, event_source=WAKE_SOURCE_POLLING, stats=stats)
            except WorkerError as exc:
                self._mark_event(event, status="error", error=str(exc))
                stats.wake_errors += 1
                continue
            stats.wakes_sent += 1

        # Sweep orphaned event entries after waking: anything rediscovered this
        # cycle had last_attempt_at refreshed above, so only true orphans age out.
        self._prune_events(stats)
        # Drop stale D4 rate-limit entries; cheap O(n) sweep.
        self._prune_recent_resume_attempts()
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

    async def _prepare_session_for_event(self, event: WakeEvent) -> None:
        persona_state = self._persona_state(event.persona)
        context_key = wake_context_key(event)
        legacy_context_key = persona_state.get("last_wake_context_key")
        if legacy_context_key is None and persona_state.get("last_wake_object_id") is not None:
            legacy_context_key = str(persona_state["last_wake_object_id"])
            context_key_for_compare = event.object_id
        else:
            context_key_for_compare = context_key
        if not should_reset_session_for_context(
            last_wake_context_key=legacy_context_key,
            wake_context_key=context_key_for_compare,
        ):
            return
        persona_state.pop("claude_session_id", None)
        persona_state.pop("runtime_session_id", None)
        self._state_dirty = True
        if isinstance(self.backend, PersonaAgentWakeBackend):
            await self.backend.reset_session()
        self._save_state_if_needed(force=True)

    async def _wake_event(
        self,
        event: WakeEvent,
        *,
        event_source: str = WAKE_SOURCE_POLLING,
        stats: RuntimeWakerStats | None = None,
    ) -> None:
        # Self-heal TTL gate (Phase 2 plan §D3/D4 / §A4): the polling path
        # filters via _should_skip_event at _run_once_async; SSE / replay paths
        # enter here directly and need the event-cooldown guard so reconnect
        # replays don't re-wake an event we just woke. We use _event_in_cooldown
        # (NOT _should_skip_event) because SSE/replay must NOT apply the
        # persona-level single-flight guard — that would suppress distinct
        # fingerprints piled up during disconnect (breaks A2 漏事件率 = 0).
        # --force bypasses (matches polling semantics).
        if not self.config.force and self._event_in_cooldown(event):
            if stats is not None:
                stats.wake_skips += 1
            return
        # D4 client-side rate limit (Phase 2): skip the resume attempt when the
        # same fingerprint was woken within the configured window. The
        # server-side UNIQUE gate (D6) still handles cross-process dedup; this
        # is a cheap in-process gate that avoids redundant round-trips during
        # SSE replay storms. Replay events bypass — they already came from a
        # ``unread_only=true`` backfill and the server UNIQUE gate will reject
        # any cross-process duplicates.
        if event_source != WAKE_SOURCE_REPLAY and self._should_skip_due_to_rate_limit(event):
            if stats is not None:
                stats.sse_rate_limit_skips += 1
            return
        # Record the resume attempt BEFORE doing work so a slow resume can't
        # race a second attempt through the same fingerprint.
        if event_source != WAKE_SOURCE_REPLAY:
            self._record_resume_attempt(event)

        await self._prepare_session_for_event(event)

        # D6 server gate: record the fingerprint against the inbound_events table
        # BEFORE any resume. If another worker (or a previous waker process) has
        # already claimed this fingerprint, the server returns 409.
        # First local sighting → server_skip (likely another worker). Heartbeat
        # re-eval after TTL → resume anyway so unfinished todos are not stuck.
        prior = self._event_state(event)
        had_prior_claim = prior.get("status") in ("woken", "server_skip")
        event_uuid = _event_uuid_for_fingerprint(self.agent_id or "", event.fingerprint)
        # v0.9 (M30A/M31 I2): legacy v1 fingerprints (``inbound:<event_id>``)
        # are routed to a separate server path that bumps ``rejection_count``
        # and returns 200 with ``status="rejected_v1"``. The waker MUST NOT
        # resume on these — they were never resumable to begin with — but the
        # audit row is preserved. Local pre-check avoids paying for a resume
        # pipeline that will be discarded immediately.
        if _is_legacy_v1_fingerprint(event.fingerprint):
            self.client.inbound_event_record(
                event_id=str(event_uuid),
                fingerprint=event.fingerprint,
                event_type=event.kind,
                source=event_source,
            )
            typer.echo(
                f"[wake:skip] v1 fingerprint rejected (rejection_count audit only): {event.fingerprint}"
            )
            self._mark_event(event, status="v1_rejected")
            return
        server_first = self.client.inbound_event_record(
            event_id=str(event_uuid),
            fingerprint=event.fingerprint,
            event_type=event.kind,
            source=event_source,
        )
        if not server_first and not had_prior_claim:
            self._mark_event(event, status="server_skip")
            return

        # D3 client gate: persist dedup state BEFORE resume. If resume crashes or
        # the process dies mid-prompt, the next start sees status=woken and the
        # self-heal TTL prevents a duplicate wake of the same fingerprint.
        self._mark_event(event, status="woken")
        self._save_state_if_needed(force=True)

        persona_state = self._persona_state(event.persona)
        session_id = self._session_id(persona_state)
        prompt = build_wake_prompt(event, project_root=self.config.project_root)
        if isinstance(self.backend, PersonaAgentWakeBackend):
            result = await self.backend.wake_async(
                prompt=prompt,
                event_id=str(event_uuid),
                event_source=event_source,
                fingerprint=event.fingerprint,
            )
        else:
            result = self.backend.wake(persona=event.persona, prompt=prompt, session_id=session_id)
        if result.skipped:
            self._mark_event(event, status="runtime_skip")
            return
        if result.session_id:
            persona_state["runtime_session_id"] = result.session_id
            persona_state["claude_session_id"] = result.session_id
            persona_state["last_session_at"] = datetime.now(UTC).isoformat()
        persona_state["last_wake_context_key"] = wake_context_key(event)
        persona_state["last_wake_object_id"] = event.object_id
        self._state_dirty = True

    def _should_skip_due_to_rate_limit(self, event: WakeEvent) -> bool:
        """D4 client-side rate limit: return True if this fingerprint was woken
        within the configured window.

        State is kept in-process (does not survive restart) — this is an
        optimization that complements, not replaces, the server-side UNIQUE
        gate. A restart clears the window; server UNIQUE rejects any cross-
        process or cross-restart duplicates regardless.
        """
        if self.config.sse_recent_resume_window_seconds <= 0:
            return False
        last_attempt = self._recent_resume_attempts.get(event.fingerprint)
        if last_attempt is None:
            return False
        return (datetime.now(UTC) - last_attempt).total_seconds() < self.config.sse_recent_resume_window_seconds

    def _record_resume_attempt(self, event: WakeEvent) -> None:
        """Stamp the current time on a fingerprint's rate-limit bucket.

        Bounded by lazy GC in ``_prune_recent_resume_attempts`` (called by
        _run_once_async + SSE replay loop) so long-running wakers don't grow
        the dict unboundedly.
        """
        self._recent_resume_attempts[event.fingerprint] = datetime.now(UTC)

    def _prune_recent_resume_attempts(self) -> None:
        """Drop entries older than the configured window. Cheap O(n) sweep."""
        if self.config.sse_recent_resume_window_seconds <= 0:
            return
        cutoff = datetime.now(UTC) - timedelta(seconds=self.config.sse_recent_resume_window_seconds * 2)
        stale = [
            fp
            for fp, ts in self._recent_resume_attempts.items()
            if ts < cutoff
        ]
        for fp in stale:
            self._recent_resume_attempts.pop(fp, None)

    # --- Phase 2 D1/D3: SSE primary path -------------------------------------

    async def _run_sse_loop_async(
        self,
        *,
        stop: asyncio.Event,
        stats: RuntimeWakerStats,
    ) -> None:
        """Run the SSE consumer loop until ``stop`` is set or cancelled.

        On every (re)connect: drain ``unread_only=true`` once to backfill
        anything missed during the disconnect window (``event_source="replay"``,
        D4 client-side rate limit bypassed per plan §D4 补漏豁免). After that,
        consume frames; on each ``notification.created`` event build a
        WakeEvent and run ``_wake_event(event_source="sse")``. The polling
        loop is the last-line fallback (D6): even with SSE down, the next
        ``_run_once_async`` cycle will rediscover any pending work.
        """
        api_url = _resolve_api_url(self.config.project_root)
        token = _resolve_bearer_token(self.config.project_root, self.config.persona)
        if not api_url or not token:
            typer.echo(
                "[sse] disabled: missing api_url or bearer token in .map/"
            )
            return
        sse_url = f"{api_url}/api/v1/agents/me/notifications/stream"
        consecutive_failures = 0
        try:
            import httpx  # local import — keep waker importable without httpx
        except ImportError:
            typer.echo("[sse] disabled: httpx not installed")
            return
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=self.config.sse_connect_timeout_seconds,
                read=self.config.sse_read_timeout_seconds,
                write=10.0,
                pool=10.0,
            ),
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            while not stop.is_set():
                stats.sse_connect_attempts += 1
                try:
                    async with client.stream(
                        "GET", sse_url, headers={"Accept": "text/event-stream"}
                    ) as response:
                        if response.status_code != 200:
                            raise RuntimeError(
                                f"SSE HTTP {response.status_code}: "
                                f"{await response.aread()!r:.200}"
                            )
                        stats.sse_connect_successes += 1
                        consecutive_failures = 0
                        await self._sse_replay_unread(stats)
                        # Phase 2 I5-A3: reconnect succeeded and replay is done.
                        # Polling 兜底 is no longer in the recovery window.
                        self._sse_recovery_in_progress = False
                        await self._sse_consume_stream(response, stats, stop)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — SSE loop must not die
                    # Phase 2 I5-A3: mark the polling-兜底 as in-SSE-recovery so
                    # any concurrent ``_run_once_async`` cycle tags itself
                    # ``recovery_excluded``. Cleared below after replay returns.
                    self._sse_recovery_in_progress = True
                    consecutive_failures += 1
                    stats.sse_disconnects += 1
                    stats.sse_last_disconnect_reason = repr(exc)[:200]
                    delay = sse_backoff_delay(
                        consecutive_failures,
                        base=self.config.sse_backoff_base_seconds,
                        cap=self.config.sse_backoff_max_seconds,
                    )
                    stats.sse_backoff_seconds_total += delay
                    typer.echo(
                        f"[sse] disconnect: {stats.sse_last_disconnect_reason}; "
                        f"backoff {delay:.1f}s"
                    )
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass

    async def _sse_consume_stream(
        self,
        response: Any,
        stats: RuntimeWakerStats,
        stop: asyncio.Event,
    ) -> None:
        """Read frames from an open SSE response and dispatch each event."""
        buffer = ""
        async for chunk in response.aiter_text():
            if stop.is_set():
                return
            buffer = (buffer + chunk).replace("\r\n", "\n")
            while True:
                frame, buffer = parse_sse_frame(buffer)
                if frame is None:
                    break
                if frame.get("event") and frame["event"] != "notification.created":
                    # Heartbeat-only frames carry no data; skip.
                    continue
                data = frame.get("data")
                if not data:
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                if payload.get("type") and payload["type"] != "notification.created":
                    continue
                stats.sse_events_received += 1
                await self._sse_dispatch_payload(payload, stats)

    async def _sse_dispatch_payload(
        self,
        payload: dict[str, Any],
        stats: RuntimeWakerStats,
    ) -> None:
        """Translate an SSE ``notification.created`` frame into a wake.

        Fetches the unread notification list to enrich the SSE frame's
        ``notification_id`` with full ``payload_json`` (which carries ``kind``
        and target ids). The fetch is cheap (one HTTP call per frame) and
        avoids a second ``GET /me/notifications/{id}`` endpoint round-trip
        just to read payload_json.
        """
        notification_id = str(payload.get("notification_id") or "")
        event_name = str(payload.get("event") or "")
        if not notification_id:
            return
        try:
            unread = self.client.notifications_unread(
                limit=self.config.sse_replay_limit,
                category="wakeable",
            )
        except WorkerError:
            return
        target = next(
            (
                item for item in unread
                if str(item.get("id") or "") == notification_id
            ),
            None,
        )
        if target is None:
            # Notification was already marked read elsewhere or isn't wakeable.
            # Polling will catch it on the next cycle if it still needs a wake.
            return
        event = self._build_sse_wake_event(target, event_name)
        if event is None:
            return
        try:
            await self._wake_event(event, event_source=WAKE_SOURCE_SSE, stats=stats)
        except WorkerError:
            # Polling will retry next cycle; SSE should keep consuming.
            return
        stats.sse_wakes_sent += 1

    def _build_sse_wake_event(
        self,
        notification: dict[str, Any],
        event_name: str,
    ) -> WakeEvent | None:
        """Build a WakeEvent from a notification row pulled via SSE.

        Fingerprint uses the notification id so each notification maps to a
        unique inbound_event row (server UNIQUE gate rejects cross-source
        duplicates regardless of which path discovered it first).
        """
        notification_id = str(notification.get("id") or "")
        if not notification_id:
            return None
        target_id = str(notification.get("target_id") or "")
        payload = notification.get("payload_json")
        if not isinstance(payload, dict):
            payload = notification.get("payload") if isinstance(notification.get("payload"), dict) else {}
        kind = _notification_event_to_wake_kind(
            str(notification.get("event") or event_name or ""),
            payload,
        )
        # Prefer payload-level target ids for topic / experiment / review_item;
        # fall back to notification.target_id. Some events omit target_id
        # (e.g. experiment_lifecycle targeting the experiment itself) — when
        # missing, use the notification id as a stable object key so the wake
        # still has a unique object_id.
        object_id = (
            str(payload.get("topic_id") or "")
            or str(payload.get("experiment_id") or "")
            or str(payload.get("item_id") or "")
            or target_id
            or notification_id
        )
        wake_version = notification.get("wake_version") or 1
        fingerprint = f"{self.config.persona}:{kind}:{notification_id}:{wake_version}"
        return WakeEvent(
            persona=self.config.persona,
            kind=kind,
            object_id=object_id,
            fingerprint=fingerprint,
            title=str(notification.get("summary") or "") or None,
            reason=f"SSE event={notification.get('event') or event_name}",
            payload=dict(notification),
        )

    async def _sse_replay_unread(self, stats: RuntimeWakerStats) -> None:
        """D3 reconnect backfill: replay unread wakeable notifications.

        Marks every replayed event with ``event_source="replay"`` so the D4
        client-side rate limit bypasses them (plan §D4 补漏豁免). The
        server-side UNIQUE gate still rejects any cross-process duplicates.
        """
        try:
            unread = self.client.notifications_unread(
                limit=self.config.sse_replay_limit,
                category="wakeable",
            )
        except WorkerError:
            return
        if not unread:
            return
        stats.sse_replay_runs += 1
        for notification in unread:
            event = self._build_sse_wake_event(
                notification, str(notification.get("event") or "")
            )
            if event is None:
                continue
            try:
                await self._wake_event(
                    event, event_source=WAKE_SOURCE_REPLAY, stats=stats
                )
            except WorkerError:
                continue
            stats.sse_replay_wakes_sent += 1

    def _session_id(self, persona_state: dict[str, Any]) -> str | None:
        sid = persona_state.get("claude_session_id") or persona_state.get("runtime_session_id")
        if sid and not persona_state.get("claude_session_id"):
            persona_state["claude_session_id"] = sid
        return str(sid) if sid else None

    def _agent_state(self, persona: str) -> dict[str, Any]:
        return self._persona_state(persona)

    def _self_heal_ttl_seconds(self) -> float:
        if self.config.heartbeat_seconds is not None:
            return self.config.heartbeat_seconds
        return self.config.woken_cooldown_seconds

    def _should_skip_event(self, event: WakeEvent) -> bool:
        # Persona-level single-flight: if this persona was woken recently and the
        # inflight window hasn't elapsed, skip ALL its events so a background
        # session still executing a prior wake is not preempted by a new one
        # waking into a different context. force bypasses this via the outer
        # _run_once_async gate (not re-checked here, matching per-event style).
        if self.config.persona_inflight_seconds > 0:
            last_woken = _parse_datetime(
                str(self._persona_state(event.persona).get("last_woken_at") or "")
            )
            # Only count wakes from THIS process (last_woken_at >= startup).
            # The timestamp persists in the state file; without this guard a
            # restart would inherit the prior process's wake and skip the whole
            # inflight window despite no session actually running here.
            if (
                last_woken is not None
                and last_woken >= self._started_at
                and (datetime.now(UTC) - last_woken).total_seconds()
                < self.config.persona_inflight_seconds
            ):
                return True
        return self._event_in_cooldown(event)

    def _event_in_cooldown(self, event: WakeEvent) -> bool:
        """Per-event cooldown check shared by polling + SSE/replay paths.

        Returns True if the event was woken / server-skip'd within the
        self-heal TTL window, or if its last attempt is still within
        ``cooldown_seconds``. Unlike ``_should_skip_event`` this does NOT
        apply persona-level single-flight, so SSE/replay paths can wake
        distinct fingerprints that happen to share a persona with a
        recently-woken event (regression guard for A2 漏事件率 = 0).

        Phase 2 plan §D4 补漏豁免 + §A4 重复唤醒率 < 0.1% both rely on this
        fingerprint-scoped check.
        """
        record = self._event_state(event)
        if not record:
            return False
        status = record.get("status")
        if status in ("woken", "server_skip"):
            # Successful claims (woken by us, or server_skip — D6 server gate
            # reported another worker claimed it) use the heartbeat / self-heal TTL.
            # Once the TTL elapses, the event is reconsidered so an agent that
            # woke but took no action is not stuck forever. Falls back to
            # last_attempt_at for state files written before woken_at existed.
            ref = _parse_datetime(
                str(record.get("woken_at") or record.get("last_attempt_at") or "")
            )
            if ref is None:
                return False
            return (
                datetime.now(UTC) - ref
            ).total_seconds() < self._self_heal_ttl_seconds()
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
        if status == "woken":
            # Stamp the self-heal TTL origin so _should_skip_event can re-evaluate
            # this event after woken_cooldown_seconds instead of skipping forever.
            record["woken_at"] = now
            # Persona-level single-flight origin: while recent, _should_skip_event
            # suppresses every other event of this persona so a running background
            # session is not preempted by a wake into a different context.
            persona_state["last_woken_at"] = now
        elif "woken_at" in record and status != "woken":
            # A non-woken transition (e.g. error) clears the woken stamp so the
            # regular cooldown path applies until it is woken again.
            del record["woken_at"]
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

    def _prune_events(self, stats: RuntimeWakerStats) -> None:
        """Drop event dedup entries that can no longer affect _should_skip_event.

        Fingerprint encodes derived fields (updated_at/comment_count for topics,
        plan version / open-count for experiments), so once the underlying object
        moves the old key is orphaned and is never matched again. Such entries
        would accumulate forever; this sweep removes them once they are older than
        the skip window. Safety invariant: threshold = max(cooldown_seconds,
        woken_cooldown_seconds) is exactly when _should_skip_event must return
        False, so anything deleted here would not have been skipped anyway.

        Runs after the wake loop: events rediscovered and woken this cycle already
        had last_attempt_at refreshed by _mark_event, so only true orphans age out.
        """
        if not self.config.prune_events:
            return
        now = datetime.now(UTC)
        threshold = max(
            self.config.cooldown_seconds,
            self._self_heal_ttl_seconds(),
        )
        pruned = 0
        personas = self.state.get("personas")
        if not isinstance(personas, dict):
            return
        for persona_state in personas.values():
            if not isinstance(persona_state, dict):
                continue
            events = persona_state.get("events")
            if not isinstance(events, dict):
                continue
            for fingerprint in list(events.keys()):
                record = events[fingerprint]
                # Non-dict anomaly: count it; clear on a real run (leave dry-run intact).
                if not isinstance(record, dict):
                    pruned += 1
                    if not self.config.dry_run:
                        del events[fingerprint]
                    continue
                # Age on last_attempt_at only; applies to both woken (with woken_at)
                # and legacy records that fall back to last_attempt_at. A record we
                # cannot age is left untouched rather than risk a wrong delete.
                last_attempt = _parse_datetime(str(record.get("last_attempt_at") or ""))
                if last_attempt is None:
                    continue
                if (now - last_attempt).total_seconds() <= threshold:
                    continue
                pruned += 1
                if not self.config.dry_run:
                    del events[fingerprint]
        if not pruned:
            return
        if self.config.dry_run:
            typer.echo(f"[dry-run] would prune {pruned} events")
        else:
            # Only a real prune mutates state; a no-op cycle stays no-write.
            self._state_dirty = True
        stats.events_pruned += pruned


def create_backend(config: RuntimeWakerConfig) -> WakeBackend:
    if config.backend == "codex":
        return CodexSdkWakeBackend(
            project_root=config.project_root,
            runtime_home=config.runtime_home,
            model=config.model,
            codex_bin=config.codex_bin,
        )
    if config.backend == "cursor":
        return CursorSdkWakeBackend(
            project_root=config.project_root,
            model=config.model,
        )
    raise WorkerError(f"Unsupported runtime backend: {config.backend}")


def _round_robin_by_kind(events: list[WakeEvent], limit: int) -> list[WakeEvent]:
    """Select up to ``limit`` events, round-robin across event kinds.

    Guarantees no single event kind monopolizes the per-cycle wake budget: as long
    as ``limit`` >= number of distinct kinds present, every kind gets at least one
    slot. This prevents e.g. several ``topic_lifecycle`` events from starving an
    ``experiment_lifecycle`` event that is queued behind them.
    """
    if limit <= 0:
        return []
    by_kind: dict[str, list[WakeEvent]] = {}
    for event in events:
        by_kind.setdefault(event.kind, []).append(event)
    selected: list[WakeEvent] = []
    while len(selected) < limit:
        progressed = False
        for group in by_kind.values():
            if not group:
                continue
            selected.append(group.pop(0))
            progressed = True
            if len(selected) >= limit:
                break
        if not progressed:
            break
    return selected


# Todo buckets mirrored from web TodosPage section order (GET /agents/me/todos).
TODO_WAKE_BUCKETS: tuple[str, ...] = (
    "mentions",
    "pending_topic_replies",
    "action_items",
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
    "action_items": "你有待跟进行动项",
    "pending_reviews": "你有实验待评审",
    "pending_result_reviews": "你有实验结果待审批",
    "pending_replies": "你有评审待回复",
    "pending_round_acks": "你有 Round Summary 待 ack",
    "pending_advance_rounds": "你有话题待推进轮次（ack 已齐）",
    "my_open_experiments": "你有进行中的实验需关注",
    "my_open_topics": "你有进行中的话题需关注",
    "notification": "你有未读通知",
}


def _todo_item_title(bucket: str, item: dict[str, Any]) -> str | None:
    for key in ("title", "topic_title", "experiment_title", "summary"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def _todo_item_stable_id(bucket: str, item: dict[str, Any]) -> str | None:
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


def _todo_item_object_id(bucket: str, item: dict[str, Any], *, stable_id: str) -> str:
    if bucket in {"pending_topic_replies", "pending_round_acks", "pending_advance_rounds"}:
        return str(item.get("topic_id") or stable_id)
    if bucket == "action_items":
        return str(item.get("topic_id") or item.get("id") or stable_id)
    if bucket in {"pending_reviews", "pending_result_reviews", "my_open_experiments"}:
        return str(item.get("id") or stable_id)
    if bucket == "pending_replies":
        return str(item.get("experiment_id") or stable_id)
    if bucket == "my_open_topics":
        return str(item.get("id") or stable_id)
    if bucket == "mentions":
        if item.get("topic_id"):
            return str(item["topic_id"])
        if item.get("experiment_id"):
            return str(item["experiment_id"])
    if bucket == "notification" and item.get("target_id"):
        return str(item["target_id"])
    return stable_id


def _todo_overlap_keys(event: WakeEvent) -> set[tuple[str, str]]:
    payload = event.payload or {}
    keys: set[tuple[str, str]] = set()
    if event.kind in {
        "pending_reviews",
        "pending_result_reviews",
        "pending_replies",
        "my_open_experiments",
    }:
        keys.add(("experiment", event.object_id))
    if event.kind == "pending_replies":
        item_id = payload.get("item_id") or payload.get("id")
        if item_id:
            keys.add(("review_item", str(item_id)))
    if event.kind in {
        "pending_topic_replies",
        "pending_advance_rounds",
        "pending_round_acks",
        "my_open_topics",
    }:
        keys.add(("topic", event.object_id))
    if event.kind == "mentions":
        if payload.get("topic_id"):
            keys.add(("topic", str(payload["topic_id"])))
        if payload.get("experiment_id"):
            keys.add(("experiment", str(payload["experiment_id"])))
        if payload.get("source_id") and payload.get("source_type"):
            keys.add((str(payload["source_type"]), str(payload["source_id"])))
    if event.kind == "action_items":
        if payload.get("id"):
            keys.add(("action_item", str(payload["id"])))
        if payload.get("topic_id"):
            keys.add(("topic", str(payload["topic_id"])))
    return keys


def _notification_overlap_keys(item: dict[str, Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    target_type = item.get("target_type")
    target_id = item.get("target_id")
    if target_type and target_id:
        keys.add((str(target_type), str(target_id)))
    payload = item.get("payload_json")
    if not isinstance(payload, dict):
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    if payload.get("topic_id"):
        keys.add(("topic", str(payload["topic_id"])))
    if payload.get("experiment_id"):
        keys.add(("experiment", str(payload["experiment_id"])))
    if payload.get("item_id"):
        keys.add(("review_item", str(payload["item_id"])))
    if payload.get("action_item_id"):
        keys.add(("action_item", str(payload["action_item_id"])))
    return keys


def discover_wake_events(
    persona: str,
    todos: dict[str, Any],
    *,
    notifications: list[dict[str, Any]] | None = None,
    ) -> list[WakeEvent]:
    """Derive wake events directly from UI-visible todos and unread notifications."""
    events: list[WakeEvent] = []
    todo_overlap_keys: set[tuple[str, str]] = set()
    for bucket in TODO_WAKE_BUCKETS:
        for item in todos.get(bucket) or []:
            if not isinstance(item, dict):
                continue
            stable_id = _todo_item_stable_id(bucket, item)
            if not stable_id:
                continue
            object_id = _todo_item_object_id(bucket, item, stable_id=stable_id)
            events.append(
                WakeEvent(
                    persona=persona,
                    kind=bucket,
                    object_id=object_id,
                    fingerprint=f"{persona}:{bucket}:{stable_id}",
                    title=_todo_item_title(bucket, item),
                    reason=f"UI todo bucket {bucket}",
                    payload=dict(item),
                )
            )
            todo_overlap_keys.update(_todo_overlap_keys(events[-1]))
    for item in notifications or []:
        if not isinstance(item, dict):
            continue
        if item.get("category") and item.get("category") != "wakeable":
            continue
        if _notification_overlap_keys(item) & todo_overlap_keys:
            continue
        stable_id = _todo_item_stable_id("notification", item)
        if not stable_id:
            continue
        wake_version = item.get("wake_version") or 1
        object_id = _todo_item_object_id("notification", item, stable_id=stable_id)
        events.append(
            WakeEvent(
                persona=persona,
                kind="notification",
                object_id=object_id,
                fingerprint=f"{persona}:notification:{stable_id}:{wake_version}",
                title=_todo_item_title("notification", item),
                reason="unread in-app notification",
                payload=dict(item),
            )
        )
    return events


def build_wake_prompt(event: WakeEvent, *, project_root: Path) -> str:
    del project_root
    command = f"map --persona {event.persona}"
    payload = event.payload or {}
    title = event.title or payload.get("topic_title") or payload.get("title") or ""
    label = TODO_BUCKET_UI_LABELS.get(event.kind, "你有待办需处理")
    lines = [f"MAP wake · {event.kind} · {event.object_id}"]
    if title:
        lines.append(f"title={title}")
    lines.append(label)
    lines.append("与 Web UI 待办/通知一致：处理完成后须让该项从 `map todos` / 通知列表消失。")
    latest_by = _wake_latest_by(event, payload)
    if latest_by:
        lines.append(f"latest_by={latest_by}")
    excerpt = _wake_excerpt(event, payload)
    if excerpt:
        lines.append(f"excerpt={excerpt}")
    for hint in _wake_command_hints(event, payload, command=command):
        lines.append(hint)
    return "\n".join(lines) + "\n"


def _wake_latest_by(event: WakeEvent, payload: dict[str, Any]) -> str | None:
    if event.kind in {"mentions", "pending_topic_replies"}:
        return (
            payload.get("author_name")
            or payload.get("last_comment_author_name")
            or payload.get("author_agent_id")
            or payload.get("last_comment_author_agent_id")
        )
    if event.kind == "pending_reviews":
        return payload.get("creator_name")
    if event.kind in {"pending_round_acks", "pending_advance_rounds"}:
        return payload.get("topic_title")
    if event.kind == "notification":
        return payload.get("event")
    return None


def _wake_excerpt(event: WakeEvent, payload: dict[str, Any]) -> str | None:
    raw = (
        payload.get("excerpt")
        or payload.get("summary_excerpt")
        or payload.get("last_comment_excerpt")
        or payload.get("summary")
        or payload.get("content")
    )
    if not raw:
        return None
    text = str(raw).strip().replace("\n", " ")
    limit = 200
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _wake_command_hints(
    event: WakeEvent, payload: dict[str, Any], *, command: str
) -> list[str]:
    topic_id = str(payload.get("topic_id") or event.object_id)
    if event.kind == "mentions":
        hints = [f"→ `{command} todos`"]
        if payload.get("topic_id"):
            hints.append(f"→ `{command} topic show --id {payload['topic_id']}`")
        if payload.get("experiment_id"):
            hints.append(f"→ `{command} experiment status --id {payload['experiment_id']}`")
        if payload.get("id"):
            hints.append(f"→ 处理后 `{command} mention dismiss --id {payload['id']}`")
        return hints
    if event.kind == "pending_topic_replies":
        comment_id = payload.get("comment_id")
        hints = [f"→ `{command} topic show --id {topic_id}`"]
        if comment_id:
            hints.append(f"reply_to={comment_id}")
        return hints
    if event.kind == "pending_advance_rounds":
        return [
            f"→ `{command} topic advance-round --id {topic_id}`",
            f"→ `{command} topic show --id {topic_id}`",
        ]
    if event.kind == "pending_round_acks":
        return [
            f"→ `{command} topic advance-round --id {topic_id} --ack accept`",
            f"→ `{command} topic show --id {topic_id}`",
        ]
    if event.kind == "action_items":
        return [
            f"→ `{command} topic show --id {topic_id}`",
            f"→ `{command} todos`",
        ]
    if event.kind in {"pending_reviews", "pending_result_reviews", "my_open_experiments"}:
        return [f"→ `{command} experiment status --id {event.object_id}`"]
    if event.kind == "pending_replies":
        experiment_id = payload.get("experiment_id") or event.object_id
        return [f"→ `{command} experiment status --id {experiment_id}`"]
    if event.kind == "my_open_topics":
        return [
            f"→ `{command} topic show --id {topic_id}`",
            f"→ 若无动作可 `{command} topic dismiss --id {topic_id}`（与 UI ✕ 相同）",
        ]
    if event.kind == "notification":
        hints = [f"→ `{command} todos`"]
        if payload.get("id"):
            hints.append(f"→ 处理后 `{command} notification read --id {payload['id']}`")
        return hints
    return [f"→ `{command} todos`"]


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
    woken_cooldown_seconds: float = typer.Option(
        1800.0,
        "--woken-cooldown-seconds",
        min=0.0,
        help="How long a woken event stays deduped before self-heal re-evaluation.",
    ),
    heartbeat_seconds: float | None = typer.Option(
        None,
        "--heartbeat-seconds",
        min=0.0,
        help=(
            "Re-wake interval when todos still show pending work (woken/server_skip). "
            "Defaults to poll --interval via start script; when unset here, uses "
            "--woken-cooldown-seconds."
        ),
    ),
    persona_inflight_seconds: float = typer.Option(
        1800.0,
        "--persona-inflight-seconds",
        min=0.0,
        help=(
            "After a successful wake, skip ALL of this persona's events for this "
            "many seconds so a background session is not preempted by another event "
            "waking into a different context. 0 disables (pure per-event dedup)."
        ),
    ),
    state_file: Path | None = typer.Option(Path(".map/runtime-waker-state.json"), "--state-file"),
    backend: str = typer.Option(
        "claude",
        "--backend",
        help="Runtime backend: claude, codex, or cursor.",
    ),
    model: str | None = typer.Option(None, "--model", help="Optional runtime model override."),
    runtime_home: Path | None = typer.Option(None, "--runtime-home", help="Optional HOME for the runtime process."),
    codex_bin: str | None = typer.Option(None, "--codex-bin", help="Optional Codex binary path for --backend codex."),
    force: bool = typer.Option(False, "--force", help="Wake even if the event was already handled."),
    prune_events: bool = typer.Option(
        True,
        "--prune-events/--no-prune-events",
        help="Drop orphaned event dedup entries once they pass the TTL sweep window (default on).",
    ),
    sse_enabled: bool = typer.Option(
        True,
        "--sse-enabled/--no-sse",
        help=(
            "Phase 2 D1: enable SSE primary path. When on, the waker maintains "
            "a long-poll to /me/notifications/stream in parallel with polling; "
            "disable only for offline tests."
        ),
    ),
    sse_recent_resume_window_seconds: float = typer.Option(
        60.0,
        "--sse-recent-resume-window",
        min=0.0,
        help=(
            "Phase 2 D4: skip wake when same fingerprint was resumed within this "
            "window. In-process only; server UNIQUE gate is unaffected. 0 disables."
        ),
    ),
    sse_backoff_base_seconds: float = typer.Option(
        1.0,
        "--sse-backoff-base",
        min=0.1,
        help="Phase 2 D3: exponential backoff base (first retry delay).",
    ),
    sse_backoff_max_seconds: float = typer.Option(
        30.0,
        "--sse-backoff-max",
        min=1.0,
        help="Phase 2 D3: exponential backoff cap.",
    ),
    sse_read_timeout_seconds: float = typer.Option(
        90.0,
        "--sse-read-timeout",
        min=10.0,
        help="Phase 2 D1: per-read timeout for the SSE stream (seconds).",
    ),
    sse_replay_limit: int = typer.Option(
        200,
        "--sse-replay-limit",
        min=1,
        help="Phase 2 D3: max unread wakeable notifications to replay on (re)connect.",
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
        woken_cooldown_seconds=woken_cooldown_seconds,
        heartbeat_seconds=heartbeat_seconds,
        persona_inflight_seconds=persona_inflight_seconds,
        state_file=state_file_path,
        project_root=root,
        map_cmd=map_cmd,
        backend=backend,
        model=model,
        runtime_home=runtime_home_path,
        codex_bin=codex_bin,
        force=force,
        prune_events=prune_events,
        sse_enabled=sse_enabled,
        sse_recent_resume_window_seconds=sse_recent_resume_window_seconds,
        sse_backoff_base_seconds=sse_backoff_base_seconds,
        sse_backoff_max_seconds=sse_backoff_max_seconds,
        sse_read_timeout_seconds=sse_read_timeout_seconds,
        sse_replay_limit=sse_replay_limit,
    )
    client = MapCommandClient(map_cmd=map_cmd, persona=persona, project_root=root, dry_run=dry_run)
    stats = RuntimeWaker(client=client, config=cfg).run_forever()
    typer.echo(json.dumps(asdict(stats), ensure_ascii=False, indent=2))


def main() -> None:
    APP()


if __name__ == "__main__":
    main()
