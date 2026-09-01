"""Per-project experiment execution lock (CLI side).

Implements CP-1 / CP-2 / CP-2.5 / CP-3 / CP-3.5 from the v2 plan:

* `acquire / release / is_held / force_release` for `experiments.status` semantics.
* Local ``fcntl.flock`` guard (best-effort, processes per host) **plus** server-side
  authoritative state stored on the ``Experiment`` row.
* TTL self-healing (server side is the source of truth).
* ``MAP_HOST_NO_LOCK`` / ``MAP_HOST_LOCK_DRY_RUN`` toggles for ops rollback.

The lock intentionally operates at the *project* level: the same ``project_id``
may only have **one** experiment whose ``phase == running`` AND
``lock_holder_experiment_id`` set at a time. Other host workers running
``execute_experiment`` against the same project will receive a ``lock_busy``
result and follow the skip-backoff policy (host execute_experiment callers).
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import logging
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

logger = logging.getLogger("map.experiment_lock")

# ---------------------------------------------------------------------------
# Constants (must satisfy lock_ttl_seconds >= max(lock_timeout, SLA) * 3)
# ---------------------------------------------------------------------------

DEFAULT_LOCK_TIMEOUT_SECONDS = 600
DEFAULT_LOCK_TTL_SECONDS = 1800
DEFAULT_SLA_SECONDS = 600  # ceiling for one execute_experiment cycle

# Skip-backoff (closed loop): exponential, capped at 30 minutes.
SKIP_BACKOFF_CAP_SECONDS = 1800
SKIP_BACKOFF_BASE_SECONDS = 60
SKIP_STUCK_THRESHOLD = 10  # when lock_skip_count >= N -> emit lock_stuck

# Log keywords (consumed by tests + ops dashboards).
LOG_ACQUIRE = "acquire_lock"
LOG_RELEASE = "release_lock"
LOG_TIMEOUT = "lock_timeout"
LOG_STUCK = "lock_stuck"
LOG_FORCE = "force_release_lock"
LOG_TTL_MISCONFIG = "lock_ttl_misconfig"
LOG_BUSY = "lock_busy"
LOG_DRY_RUN = "lock_dry_run"


class ExperimentLockError(RuntimeError):
    """Raised when the lock module is misconfigured."""


class LockBusy(Exception):
    """Raised when an experiment cannot acquire the lock for this project."""

    def __init__(self, project_id: str, holder: str | None, message: str = "lock busy") -> None:
        super().__init__(message)
        self.project_id = project_id
        self.holder = holder


@dataclass(frozen=True)
class LockConfig:
    """Runtime configuration for the lock module.

    The TTL/timeout/SLA coupling rule is enforced at construction time:

        lock_ttl_seconds >= max(lock_timeout_seconds, sla_seconds) * 3
    """

    lock_timeout_seconds: int = DEFAULT_LOCK_TIMEOUT_SECONDS
    lock_ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS
    sla_seconds: int = DEFAULT_SLA_SECONDS
    skip_backoff_base_seconds: int = SKIP_BACKOFF_BASE_SECONDS
    skip_backoff_cap_seconds: int = SKIP_BACKOFF_CAP_SECONDS
    skip_stuck_threshold: int = SKIP_STUCK_THRESHOLD

    def __post_init__(self) -> None:
        required = max(self.lock_timeout_seconds, self.sla_seconds) * 3
        if self.lock_ttl_seconds < required:
            raise ExperimentLockError(
                f"lock_ttl_misconfig: lock_ttl_seconds={self.lock_ttl_seconds} "
                f"must be >= max(lock_timeout_seconds, sla_seconds) * 3 = {required}"
            )


@dataclass
class LockState:
    """Server-side authoritative lock snapshot for a single experiment."""

    project_id: str
    lock_holder_experiment_id: str | None = None
    lock_acquired_at: str | None = None
    lock_ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS
    next_attempt_at: str | None = None
    lock_skip_count: int = 0

    @property
    def is_held(self) -> bool:
        return bool(self.lock_holder_experiment_id)

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if not self.lock_acquired_at:
            return True
        try:
            acquired = datetime.fromisoformat(self.lock_acquired_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        reference = now or datetime.now(timezone.utc)
        if acquired.tzinfo is None:
            acquired = acquired.replace(tzinfo=timezone.utc)
        return reference >= acquired + timedelta(seconds=self.lock_ttl_seconds)


# ---------------------------------------------------------------------------
# Backend protocol — keeps the module testable.
# ---------------------------------------------------------------------------


class LockBackend(Protocol):
    """Abstract server-side storage for lock state.

    The real backend is the ``experiments`` table (see
    ``cli.map_command_client.MapCommandClient``); tests can inject an
    in-memory implementation.
    """

    def get_lock_state(self, project_id: str) -> LockState: ...
    def write_lock(self, project_id: str, experiment_id: str, *, ttl: int) -> LockState: ...
    def clear_lock(self, project_id: str, *, experiment_id: str | None) -> bool: ...
    def bump_skip_count(self, project_id: str, experiment_id: str, *, next_attempt_at: str) -> LockState: ...


# ---------------------------------------------------------------------------
# In-memory backend for unit tests
# ---------------------------------------------------------------------------


@dataclass
class InMemoryLockBackend:
    """A minimal backend used by unit tests."""

    states: dict[str, LockState] = field(default_factory=dict)

    def get_lock_state(self, project_id: str) -> LockState:
        return self.states.setdefault(project_id, LockState(project_id=project_id))

    def write_lock(self, project_id: str, experiment_id: str, *, ttl: int) -> LockState:
        state = self.get_lock_state(project_id)
        state.lock_holder_experiment_id = experiment_id
        state.lock_acquired_at = datetime.now(timezone.utc).isoformat()
        state.lock_ttl_seconds = ttl
        return state

    def clear_lock(self, project_id: str, *, experiment_id: str | None = None) -> bool:
        state = self.states.get(project_id)
        if state is None:
            return False
        if experiment_id is not None and state.lock_holder_experiment_id != experiment_id:
            return False
        state.lock_holder_experiment_id = None
        state.lock_acquired_at = None
        return True

    def bump_skip_count(self, project_id: str, experiment_id: str, *, next_attempt_at: str) -> LockState:
        # In-memory backend keeps skip count on the experiment's *own* state row;
        # tests assert via a separate per-experiment slot.
        state = self.get_lock_state(project_id)
        state.lock_skip_count += 1
        state.next_attempt_at = next_attempt_at
        return state


# ---------------------------------------------------------------------------
# Local file lock (best-effort, intra-host)
# ---------------------------------------------------------------------------


def _local_lock_path(project_id: str, *, base_dir: Path | None = None) -> Path:
    base = base_dir or Path(os.environ.get("MAP_HOST_LOCK_DIR", "/tmp/map-host-locks"))
    base.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in project_id)
    return base / f"exp-lock-{safe}.lock"


@contextlib.contextmanager
def _local_flock(path: Path) -> Iterator[None]:
    """Try to grab an exclusive flock; non-blocking by default.

    Raises ``LockBusy`` if another host process already holds the local lock.
    """

    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise LockBusy("local", holder="local-process") from exc
            raise
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Manager — orchestrates acquire/release/is_held/force_release
# ---------------------------------------------------------------------------


@dataclass
class ExperimentLockManager:
    """Coordinates local + server-side lock state for ``execute_experiment``."""

    backend: LockBackend
    config: LockConfig = field(default_factory=LockConfig)
    local_lock_dir: Path | None = None
    dry_run: bool = False
    disabled: bool = False

    # ---- high-level API ----

    def acquire(self, *, project_id: str, experiment_id: str) -> LockState:
        """Try to grab the lock for ``experiment_id``.

        On success returns the new ``LockState``. On contention or TTL expiry,
        raises ``LockBusy`` and **does not** mutate state.
        """
        if self.disabled:
            return LockState(project_id=project_id, lock_holder_experiment_id=experiment_id)
        if self.dry_run:
            logger.info("%s project=%s experiment=%s", LOG_DRY_RUN, project_id, experiment_id)
            return LockState(project_id=project_id, lock_holder_experiment_id=experiment_id)

        state = self.backend.get_lock_state(project_id)
        if state.is_held and not state.is_expired() and state.lock_holder_experiment_id != experiment_id:
            logger.info("%s project=%s holder=%s", LOG_BUSY, project_id, state.lock_holder_experiment_id)
            raise LockBusy(project_id, state.lock_holder_experiment_id)

        lock_path = _local_lock_path(project_id, base_dir=self.local_lock_dir)
        try:
            with _local_flock(lock_path):
                # Re-check server state under local lock to avoid TOCTOU.
                state = self.backend.get_lock_state(project_id)
                if state.is_held and not state.is_expired() and state.lock_holder_experiment_id != experiment_id:
                    logger.info("%s project=%s holder=%s", LOG_BUSY, project_id, state.lock_holder_experiment_id)
                    raise LockBusy(project_id, state.lock_holder_experiment_id)
                new_state = self.backend.write_lock(project_id, experiment_id, ttl=self.config.lock_ttl_seconds)
                logger.info(
                    "%s project=%s experiment=%s ttl=%s",
                    LOG_ACQUIRE,
                    project_id,
                    experiment_id,
                    self.config.lock_ttl_seconds,
                )
                return new_state
        except LockBusy:
            raise
        except OSError as exc:
            raise ExperimentLockError(f"local lock failed for project {project_id}: {exc}") from exc

    def release(self, *, project_id: str, experiment_id: str) -> bool:
        """Release the lock for ``experiment_id``.

        Safe to call when the caller no longer holds the lock — used in
        ``finally`` blocks.
        """
        if self.disabled:
            return True
        if self.dry_run:
            logger.info("%s project=%s experiment=%s", LOG_DRY_RUN, project_id, experiment_id)
            return True
        ok = self.backend.clear_lock(project_id, experiment_id=experiment_id)
        logger.info("%s project=%s experiment=%s ok=%s", LOG_RELEASE, project_id, experiment_id, ok)
        return ok

    def is_held(self, *, project_id: str) -> bool:
        state = self.backend.get_lock_state(project_id)
        if not state.is_held:
            return False
        return not state.is_expired()

    def force_release(self, *, project_id: str, reason: str, actor: str | None = None) -> LockState:
        """Operator override: clear the lock unconditionally and audit-log it."""
        state = self.backend.get_lock_state(project_id)
        previous_holder = state.lock_holder_experiment_id
        self.backend.clear_lock(project_id)
        audit = {
            "action": LOG_FORCE,
            "project_id": project_id,
            "previous_holder": previous_holder,
            "reason": reason,
            "actor": actor,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        logger.warning("%s %s", LOG_FORCE, json.dumps(audit, ensure_ascii=False, sort_keys=True))
        return self.backend.get_lock_state(project_id)

    # ---- skip closed-loop helpers ----

    def compute_backoff(self, *, skip_count: int) -> int:
        """Exponential backoff capped at ``skip_backoff_cap_seconds``.

        ``skip_count`` is the value *after* it has been incremented, so the
        first failure receives ``60 * 2^0 = 60s``.
        """
        if skip_count < 0:
            skip_count = 0
        candidate = self.config.skip_backoff_base_seconds * (2 ** (skip_count - 1))
        return min(int(candidate), int(self.config.skip_backoff_cap_seconds))

    def record_skip(self, *, project_id: str, experiment_id: str, now: datetime | None = None) -> tuple[int, str]:
        """Increment ``lock_skip_count`` and compute ``next_attempt_at``.

        Returns ``(new_skip_count, iso_next_attempt_at)``. Emits ``lock_stuck``
        when threshold is crossed.
        """
        reference = now or datetime.now(timezone.utc)
        prior = self.backend.get_lock_state(project_id).lock_skip_count
        new_count = prior + 1
        backoff = self.compute_backoff(skip_count=new_count)
        next_at = (reference + timedelta(seconds=backoff)).isoformat()
        self.backend.bump_skip_count(project_id, experiment_id, next_attempt_at=next_at)
        if new_count >= self.config.skip_stuck_threshold:
            logger.warning(
                "%s project=%s experiment=%s skip_count=%s next_attempt_at=%s",
                LOG_STUCK,
                project_id,
                experiment_id,
                new_count,
                next_at,
            )
        return new_count, next_at

    # ---- env toggles ----

    @classmethod
    def from_env(cls, backend: LockBackend) -> ExperimentLockManager:
        disabled = os.environ.get("MAP_HOST_NO_LOCK") == "1"
        dry_run = os.environ.get("MAP_HOST_LOCK_DRY_RUN") == "1"
        if disabled:
            logger.warning("MAP_HOST_NO_LOCK=1 -> experiment lock disabled (rollback mode)")
        if dry_run:
            logger.warning("MAP_HOST_LOCK_DRY_RUN=1 -> experiment lock dry-run mode")
        return cls(backend=backend, dry_run=dry_run, disabled=disabled)


# ---------------------------------------------------------------------------
# Helpers for env config
# ---------------------------------------------------------------------------


def env_lock_disabled() -> bool:
    return os.environ.get("MAP_HOST_NO_LOCK") == "1"


def env_lock_dry_run() -> bool:
    return os.environ.get("MAP_HOST_LOCK_DRY_RUN") == "1"


def new_lock_id() -> str:
    """Generate a fresh lock attempt id (UUID4)."""
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Re-exports
# ---------------------------------------------------------------------------
__all__ = [
    "DEFAULT_LOCK_TIMEOUT_SECONDS",
    "DEFAULT_LOCK_TTL_SECONDS",
    "DEFAULT_SLA_SECONDS",
    "ExperimentLockError",
    "ExperimentLockManager",
    "InMemoryLockBackend",
    "LockBackend",
    "LockBusy",
    "LockConfig",
    "LockState",
    "LOG_ACQUIRE",
    "LOG_BUSY",
    "LOG_DRY_RUN",
    "LOG_FORCE",
    "LOG_RELEASE",
    "LOG_STUCK",
    "LOG_TIMEOUT",
    "LOG_TTL_MISCONFIG",
    "SKIP_BACKOFF_BASE_SECONDS",
    "SKIP_BACKOFF_CAP_SECONDS",
    "SKIP_STUCK_THRESHOLD",
    "env_lock_disabled",
    "env_lock_dry_run",
    "new_lock_id",
]
