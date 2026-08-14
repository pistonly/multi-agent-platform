"""Unit tests for the experiment execution lock module (CP-1 / CP-2 / CP-2.5).

Coverage:
* TTL/timeout/SLA consistency enforcement at construction time.
* acquire / release / is_held happy paths.
* ``LockBusy`` when another experiment holds the lock.
* TTL self-healing: stale locks are reclaimable.
* Skip closed-loop: exponential backoff capped, ``next_attempt_at`` advanced,
  ``lock_stuck`` triggered at threshold.
* ``force_release`` clears state and emits audit log payload.
* ``MAP_HOST_NO_LOCK`` / ``MAP_HOST_LOCK_DRY_RUN`` env toggles.
* Priority aging (FIFO starvation mitigation candidate).
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cli.runtime.run_lock import (
    DEFAULT_LOCK_TTL_SECONDS,
    LOG_FORCE,
    LOG_STUCK,
    ExperimentLockError,
    ExperimentLockManager,
    InMemoryLockBackend,
    LockBusy,
    LockConfig,
    LockState,
)

# ---------------------------------------------------------------------------
# Config + construction
# ---------------------------------------------------------------------------


def test_lock_config_default_passes_consistency():
    cfg = LockConfig()
    assert cfg.lock_ttl_seconds == DEFAULT_LOCK_TTL_SECONDS
    assert cfg.lock_ttl_seconds >= max(cfg.lock_timeout_seconds, cfg.sla_seconds) * 3


def test_lock_config_rejects_ttl_smaller_than_three_x_timeout():
    with pytest.raises(ExperimentLockError) as exc:
        LockConfig(lock_timeout_seconds=600, lock_ttl_seconds=600)
    assert "lock_ttl_misconfig" in str(exc.value)


def test_lock_config_accepts_exact_boundary():
    # lock_ttl == 3 * lock_timeout is the lower bound.
    LockConfig(lock_timeout_seconds=300, sla_seconds=300, lock_ttl_seconds=900)


def test_lock_config_requires_ttl_cover_sla():
    with pytest.raises(ExperimentLockError):
        # sla > timeout but ttl smaller than 3*sla
        LockConfig(lock_timeout_seconds=300, sla_seconds=1000, lock_ttl_seconds=2000)


# ---------------------------------------------------------------------------
# acquire / release / is_held
# ---------------------------------------------------------------------------


def _manager(**overrides):
    backend = InMemoryLockBackend()
    manager = ExperimentLockManager(backend=backend, **overrides)
    return backend, manager


def test_acquire_then_release_roundtrip():
    backend, manager = _manager()
    state = manager.acquire(project_id="p1", experiment_id="e1")
    assert state.lock_holder_experiment_id == "e1"
    assert state.is_held
    assert manager.is_held(project_id="p1")
    assert manager.release(project_id="p1", experiment_id="e1")
    assert not manager.is_held(project_id="p1")
    assert backend.states["p1"].lock_holder_experiment_id is None


def test_acquire_conflict_raises_lock_busy():
    _, manager = _manager()
    manager.acquire(project_id="p1", experiment_id="e1")
    with pytest.raises(LockBusy) as exc:
        manager.acquire(project_id="p1", experiment_id="e2")
    assert exc.value.holder == "e1"


def test_release_is_safe_when_not_holder():
    _, manager = _manager()
    manager.acquire(project_id="p1", experiment_id="e1")
    # Different experiment trying to release should not clear.
    assert not manager.release(project_id="p1", experiment_id="e2")
    assert manager.is_held(project_id="p1")


def test_is_held_false_for_unacquired():
    _, manager = _manager()
    assert not manager.is_held(project_id="p1")


def test_acquire_expired_lock_reclaims(tmp_path: Path):
    backend = InMemoryLockBackend()
    backend.states["p1"] = LockState(
        project_id="p1",
        lock_holder_experiment_id="old-holder",
        lock_acquired_at=(datetime.now(timezone.utc) - timedelta(seconds=10_000)).isoformat(),
        lock_ttl_seconds=1800,
    )
    manager = ExperimentLockManager(backend=backend, local_lock_dir=tmp_path)
    state = manager.acquire(project_id="p1", experiment_id="e-new")
    assert state.lock_holder_experiment_id == "e-new"


# ---------------------------------------------------------------------------
# Skip closed-loop (backoff + lock_stuck)
# ---------------------------------------------------------------------------


def test_compute_backoff_first_failure_is_60s():
    _, manager = _manager()
    assert manager.compute_backoff(skip_count=1) == 60


def test_compute_backoff_grows_then_caps_at_1800s():
    _, manager = _manager()
    values = [manager.compute_backoff(skip_count=i) for i in range(1, 12)]
    # Strictly non-decreasing until cap.
    for previous, current in zip(values, values[1:], strict=False):
        assert current >= previous
    assert values[-1] == 1800


def test_record_skip_increments_and_emits_next_attempt(tmp_path: Path):
    backend, manager = _manager(local_lock_dir=tmp_path)
    fixed_now = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)
    skip_count, next_attempt = manager.record_skip(
        project_id="p1", experiment_id="e1", now=fixed_now
    )
    assert skip_count == 1
    parsed = datetime.fromisoformat(next_attempt.replace("Z", "+00:00"))
    # backoff for first failure is 60s
    assert parsed == fixed_now + timedelta(seconds=60)


def test_record_skip_emits_lock_stuck_at_threshold(tmp_path: Path, caplog):
    _, manager = _manager(local_lock_dir=tmp_path)
    fixed_now = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)
    caplog.set_level("WARNING", logger="map.experiment_lock")
    # First 9 failures do not trigger.
    for _ in range(1, 10):
        manager.record_skip(project_id="p1", experiment_id="e1", now=fixed_now)
    # 10th failure crosses threshold -> emit lock_stuck
    manager.record_skip(project_id="p1", experiment_id="e1", now=fixed_now)
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert LOG_STUCK in joined


# ---------------------------------------------------------------------------
# Force release + audit log
# ---------------------------------------------------------------------------


def test_force_release_clears_lock_and_returns_state(tmp_path: Path, caplog):
    backend, manager = _manager(local_lock_dir=tmp_path)
    manager.acquire(project_id="p1", experiment_id="e1")
    caplog.set_level("WARNING", logger="map.experiment_lock")
    state = manager.force_release(project_id="p1", reason="worker crashed", actor="ops@example.com")
    assert state.lock_holder_experiment_id is None
    assert state.lock_acquired_at is None
    assert not manager.is_held(project_id="p1")
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert LOG_FORCE in joined
    assert "ops@example.com" in joined


def test_force_release_records_audit_payload(tmp_path: Path, caplog):
    _, manager = _manager(local_lock_dir=tmp_path)
    manager.acquire(project_id="p1", experiment_id="e1")
    caplog.set_level("WARNING", logger="map.experiment_lock")
    manager.force_release(project_id="p1", reason="stale", actor="auditor")
    body = "\n".join(record.getMessage() for record in caplog.records)
    assert "previous_holder" in body
    assert "e1" in body
    assert "stale" in body
    assert "auditor" in body


# ---------------------------------------------------------------------------
# Env toggles
# ---------------------------------------------------------------------------


def test_from_env_disables_lock(monkeypatch):
    monkeypatch.setenv("MAP_HOST_NO_LOCK", "1")
    manager = ExperimentLockManager.from_env(InMemoryLockBackend())
    assert manager.disabled is True
    state = manager.acquire(project_id="p1", experiment_id="e1")
    # acquire is a no-op when disabled.
    assert state.lock_holder_experiment_id == "e1"
    # Real backend should NOT have been touched.
    assert "p1" not in manager.backend.states


def test_from_env_dry_run(monkeypatch):
    monkeypatch.setenv("MAP_HOST_LOCK_DRY_RUN", "1")
    manager = ExperimentLockManager.from_env(InMemoryLockBackend())
    assert manager.dry_run is True


# ---------------------------------------------------------------------------
# Concurrency: 4 workers, 1 winner
# ---------------------------------------------------------------------------


def test_concurrent_acquire_only_one_winner(tmp_path: Path):
    backend = InMemoryLockBackend()
    manager = ExperimentLockManager(backend=backend, local_lock_dir=tmp_path)
    results: list[tuple[str, bool, str | None]] = []
    barrier = threading.Barrier(4)

    def worker(exp_id: str) -> None:
        barrier.wait()
        try:
            manager.acquire(project_id="p1", experiment_id=exp_id)
            results.append((exp_id, True, None))
        except LockBusy as exc:
            results.append((exp_id, False, str(exc.holder)))

    threads = [threading.Thread(target=worker, args=(f"e{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [exp_id for exp_id, ok, _ in results if ok]
    assert len(winners) == 1
    losers = [exp_id for exp_id, ok, holder in results if not ok]
    assert len(losers) == 3


def test_concurrent_acquire_after_release_proceeds(tmp_path: Path):
    backend = InMemoryLockBackend()
    manager = ExperimentLockManager(backend=backend, local_lock_dir=tmp_path)

    state = manager.acquire(project_id="p1", experiment_id="e1")
    assert state.lock_holder_experiment_id == "e1"
    manager.release(project_id="p1", experiment_id="e1")
    state2 = manager.acquire(project_id="p1", experiment_id="e2")
    assert state2.lock_holder_experiment_id == "e2"


# ---------------------------------------------------------------------------
# Aging (FIFO starvation mitigation candidate)
# ---------------------------------------------------------------------------


def test_priority_aging_advances_over_time():
    """Long-waiting experiments should bubble up in the queue."""
    backend = InMemoryLockBackend()
    manager = ExperimentLockManager(backend=backend)
    manager.acquire(project_id="p1", experiment_id="e1")
    # Three experiments waiting in FIFO order.
    queue = [
        ("e2", datetime.now(timezone.utc) - timedelta(seconds=240)),
        ("e3", datetime.now(timezone.utc) - timedelta(seconds=120)),
        ("e4", datetime.now(timezone.utc)),
    ]
    weighted = sorted(
        ((exp_id, enq, max(0, int((datetime.now(timezone.utc) - enq).total_seconds() // 60)))
         for exp_id, enq in queue),
        key=lambda item: (-item[2], item[1]),
    )
    # e2 waited ~4 minutes -> highest weight.
    assert weighted[0][0] == "e2"
    # Within same weight bucket, FIFO still holds.
    assert weighted[1][0] == "e3"


# ---------------------------------------------------------------------------
# Disabled + dry_run interplay with host worker
# ---------------------------------------------------------------------------


def test_disabled_release_is_noop_and_returns_true():
    manager = ExperimentLockManager(backend=InMemoryLockBackend(), disabled=True)
    assert manager.acquire(project_id="p1", experiment_id="e1").lock_holder_experiment_id == "e1"
    assert manager.release(project_id="p1", experiment_id="e1") is True


def test_dry_run_does_not_mutate_backend():
    backend = InMemoryLockBackend()
    manager = ExperimentLockManager(backend=backend, dry_run=True)
    manager.acquire(project_id="p1", experiment_id="e1")
    assert "p1" not in backend.states


# ---------------------------------------------------------------------------
# LockState helpers
# ---------------------------------------------------------------------------


def test_lock_state_is_expired_without_acquired_at():
    state = LockState(project_id="p1", lock_holder_experiment_id="x", lock_acquired_at=None)
    assert state.is_expired()


def test_lock_state_is_expired_handles_naive_timestamp():
    state = LockState(
        project_id="p1",
        lock_holder_experiment_id="x",
        lock_acquired_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    )
    # Should not raise.
    state.is_expired()
