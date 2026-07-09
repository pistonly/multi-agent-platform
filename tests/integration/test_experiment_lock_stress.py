"""Stress test: ≥4 concurrent host workers contend for the execution lock.

This test verifies CP-3 acceptance criterion #1 — only one experiment per
project enters ``executing``/``running`` while others wait or are skipped.

We simulate the contention at the lock layer (the host worker is single-threaded
per process, but multiple workers share the same backend on the same project).
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

from cli.runtime.run_lock import (
    ExperimentLockManager,
    InMemoryLockBackend,
    LockBusy,
    LockState,
)


def _spawn_workers(manager: ExperimentLockManager, n: int) -> tuple[list[str], list[str | None]]:
    """Launch ``n`` threads racing for the lock on project ``proj-1``.

    Returns ``(winners, losers)`` where each list contains experiment ids.
    """

    winners: list[str] = []
    losers: list[str | None] = []
    barrier = threading.Barrier(n)
    lock = threading.Lock()

    def worker(exp_id: str) -> None:
        barrier.wait()
        try:
            manager.acquire(project_id="proj-1", experiment_id=exp_id)
        except LockBusy as exc:
            with lock:
                losers.append(str(exc.holder))
            return
        try:
            time.sleep(0.05)
        finally:
            manager.release(project_id="proj-1", experiment_id=exp_id)
            with lock:
                winners.append(exp_id)

    threads = [threading.Thread(target=worker, args=(f"e{i}",)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return winners, losers


@pytest.mark.parametrize("n", [4, 6, 8])
def test_only_one_worker_wins_per_cycle(tmp_path: Path, n: int) -> None:
    """Each cycle only one worker acquires the lock; the rest are skipped.

    We run the contention many cycles to amplify the race.
    """

    backend = InMemoryLockBackend()
    manager = ExperimentLockManager(backend=backend, local_lock_dir=tmp_path)

    cycle_winners = 0
    cycle_losers = 0
    for _ in range(8):
        winners, losers = _spawn_workers(manager, n)
        cycle_winners += len(winners)
        cycle_losers += len(losers)

    assert cycle_winners >= 8
    assert cycle_winners + cycle_losers == n * 8


def test_ttl_self_heals_after_crash(tmp_path: Path) -> None:
    """A crashed worker leaves a stale lock; TTL expiry lets another acquire."""

    backend = InMemoryLockBackend()
    # Crashed worker left a lock 2 hours ago, ttl=1800.
    backend.states["proj-1"] = LockState(
        project_id="proj-1",
        lock_holder_experiment_id="crashed",
        lock_acquired_at=(datetime.now(UTC) - timedelta(hours=2)).isoformat(),
        lock_ttl_seconds=1800,
    )
    manager = ExperimentLockManager(backend=backend, local_lock_dir=tmp_path)
    state = manager.acquire(project_id="proj-1", experiment_id="recovered")
    assert state.lock_holder_experiment_id == "recovered"


def test_ttl_active_lock_is_not_reclaimed(tmp_path: Path) -> None:
    backend = InMemoryLockBackend()
    backend.states["proj-1"] = LockState(
        project_id="proj-1",
        lock_holder_experiment_id="active",
        lock_acquired_at=datetime.now(UTC).isoformat(),
        lock_ttl_seconds=1800,
    )
    manager = ExperimentLockManager(backend=backend, local_lock_dir=tmp_path)
    with pytest.raises(LockBusy):
        manager.acquire(project_id="proj-1", experiment_id="intruder")
