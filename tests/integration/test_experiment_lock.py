"""Integration tests: experiment execution lock wired into host_worker."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from cli.host_worker import HostWorker, WorkerConfig
from cli.experiment_lock import (
    LOG_BUSY,
    LOG_STUCK,
    InMemoryLockBackend,
    ExperimentLockManager,
)


class LockAwareExperimentFakeClient:
    """Test double with the lock-related surface area."""

    def __init__(
        self,
        *,
        todos: dict[str, Any],
        experiments: dict[str, dict[str, Any]],
        lock_backend: InMemoryLockBackend,
    ) -> None:
        self._todos = todos
        self._experiments = experiments
        self._lock_backend = lock_backend
        self.revisions: list[dict[str, Any]] = []
        self.approvals: list[str] = []
        self.starts: list[str] = []
        self.completions: list[dict[str, Any]] = []
        self.submits: list[str] = []
        self.events: list[dict[str, Any]] = []
        self.runner_responses: list[dict[str, Any]] = []

    def whoami(self) -> dict[str, Any]:
        return {"id": "host-agent", "name": "host"}

    def todos(self) -> dict[str, Any]:
        return self._todos

    def topic_show(self, topic_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def topic_comment(self, topic_id: str, body: str, parent_id: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def topic_advance_round(self, topic_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def experiment_create(self, title: str, plan_file: Path, *, topic_id: str, submit_for_review: bool) -> dict:
        raise NotImplementedError

    def experiment_status(self, experiment_id: str) -> dict[str, Any]:
        # Inject lock fields derived from the in-memory backend.
        detail = dict(self._experiments[experiment_id])
        project_id = str(detail.get("project_id") or "")
        state = self._lock_backend.get_lock_state(project_id)
        if state.is_held and not state.is_expired():
            detail["lock_holder_experiment_id"] = state.lock_holder_experiment_id
            detail["lock_acquired_at"] = state.lock_acquired_at
            detail["lock_ttl_seconds"] = state.lock_ttl_seconds
        detail.setdefault("lock_skip_count", 0)
        if state.next_attempt_at:
            detail["next_attempt_at"] = state.next_attempt_at
        return detail

    def experiment_submit_review(self, experiment_id: str) -> dict[str, Any]:
        self.submits.append(experiment_id)
        self._experiments[experiment_id]["phase"] = "review"
        return {"id": experiment_id}

    def experiment_reviews_list(self, experiment_id: str) -> list[dict[str, Any]]:
        return self._experiments[experiment_id].get("reviews") or []

    def plan_revise(
        self,
        experiment_id: str,
        plan_file: Path,
        *,
        note: str | None,
        addressed_item_ids: list[str],
    ) -> dict[str, Any]:
        self.revisions.append(
            {
                "experiment_id": experiment_id,
                "plan": plan_file.read_text(encoding="utf-8"),
                "note": note,
                "addressed_item_ids": addressed_item_ids,
            }
        )
        return {"version": 2}

    def experiment_approve(self, experiment_id: str) -> dict[str, Any]:
        self.approvals.append(experiment_id)
        self._experiments[experiment_id]["phase"] = "approved"
        self._experiments[experiment_id]["open_unreasonable_count"] = 0
        return {"id": experiment_id}

    def experiment_start(self, experiment_id: str) -> dict[str, Any]:
        self.starts.append(experiment_id)
        self._experiments[experiment_id]["phase"] = "running"
        return {"id": experiment_id}

    def experiment_complete(self, experiment_id: str, *, summary: str, log_file: Path) -> dict[str, Any]:
        self.completions.append(
            {"experiment_id": experiment_id, "summary": summary, "log": log_file.read_text(encoding="utf-8")}
        )
        self._experiments[experiment_id]["phase"] = "done"
        return {"id": experiment_id}

    # --- Lock surface ------------------------------------------------------

    def experiment_acquire_lock(self, experiment_id: str, *, ttl_seconds: int) -> dict[str, Any]:
        detail = self._experiments[experiment_id]
        project_id = str(detail.get("project_id") or "")
        self._lock_backend.write_lock(project_id, experiment_id, ttl=ttl_seconds)
        return {"id": experiment_id}

    def experiment_release_lock(self, experiment_id: str) -> dict[str, Any]:
        detail = self._experiments[experiment_id]
        project_id = str(detail.get("project_id") or "")
        self._lock_backend.clear_lock(project_id, experiment_id=experiment_id)
        return {"id": experiment_id}

    def experiment_force_release_lock(
        self, experiment_id: str, *, reason: str, actor: str | None = None
    ) -> dict[str, Any]:
        detail = self._experiments[experiment_id]
        project_id = str(detail.get("project_id") or "")
        self._lock_backend.clear_lock(project_id)
        return {"id": experiment_id}

    def experiment_record_skip(
        self, experiment_id: str, *, next_attempt_at: str
    ) -> dict[str, Any]:
        detail = self._experiments[experiment_id]
        project_id = str(detail.get("project_id") or "")
        state = self._lock_backend.bump_skip_count(project_id, experiment_id, next_attempt_at=next_attempt_at)
        # Persist onto detail so subsequent status reads see the values.
        detail["lock_skip_count"] = state.lock_skip_count
        detail["next_attempt_at"] = state.next_attempt_at
        return {"id": experiment_id, "lock_skip_count": state.lock_skip_count}


def _runner_script(tmp_path: Path, body: str) -> str:
    path = tmp_path / "runner.py"
    path.write_text(body, encoding="utf-8")
    return f"{sys.executable} {path}"


def test_execute_acquires_and_releases_lock(tmp_path: Path) -> None:
    """Happy path: the lock is acquired at execute, released on completion."""
    runner = _runner_script(
        tmp_path,
        """
import json, sys
req = json.loads(sys.stdin.readline())
assert req["action"] == "execute_experiment"
print(json.dumps({"summary": "ok", "execution_log_md": "# log"}))
""",
    )
    backend = InMemoryLockBackend()
    client = LockAwareExperimentFakeClient(
        todos={"my_open_experiments": [{"id": "exp-1"}], "pending_topic_replies": []},
        experiments={
            "exp-1": {
                "id": "exp-1",
                "title": "lock test",
                "phase": "running",
                "project_id": "proj-1",
                "open_unreasonable_count": 0,
                "current_plan_version": 1,
                "current_plan": {"content_md": "## plan"},
                "reviews": [],
            }
        },
        lock_backend=backend,
    )
    stats = HostWorker(
        client,
        WorkerConfig(
            once=True,
            auto_experiment_lifecycle=True,
            agent_runner=runner,
            state_file=tmp_path / "host-state.json",
            plan_dir=tmp_path,
        ),
    ).run_once()

    assert stats.experiments_completed == 1
    assert stats.lock_acquired == 1
    # Lock was released on completion.
    assert backend.get_lock_state("proj-1").lock_holder_experiment_id is None


def test_execute_lock_skip_records_backoff_and_next_attempt(tmp_path: Path) -> None:
    """When another experiment holds the lock, we increment skip and defer."""
    runner = _runner_script(
        tmp_path,
        """raise SystemExit(2)""",
    )
    backend = InMemoryLockBackend()
    # Simulate: a different host already holds the lock for proj-1.
    backend.write_lock("proj-1", "other-holder", ttl=1800)

    client = LockAwareExperimentFakeClient(
        todos={"my_open_experiments": [{"id": "exp-1"}], "pending_topic_replies": []},
        experiments={
            "exp-1": {
                "id": "exp-1",
                "title": "skipped",
                "phase": "running",
                "project_id": "proj-1",
                "open_unreasonable_count": 0,
                "current_plan_version": 1,
                "current_plan": {"content_md": "## plan"},
                "reviews": [],
                "lock_skip_count": 0,
            }
        },
        lock_backend=backend,
    )
    stats = HostWorker(
        client,
        WorkerConfig(
            once=True,
            auto_experiment_lifecycle=True,
            agent_runner=runner,
            state_file=tmp_path / "host-state.json",
            plan_dir=tmp_path,
        ),
    ).run_once()

    # Skipped (no completion, no acquire, lock_skipped counted).
    assert stats.experiments_completed == 0
    assert stats.lock_skipped == 1
    assert stats.lock_acquired == 0
    detail = client._experiments["exp-1"]
    assert detail["lock_skip_count"] == 1
    assert detail.get("next_attempt_at")
    # The other holder should still hold the lock.
    state = backend.get_lock_state("proj-1")
    assert state.lock_holder_experiment_id == "other-holder"


def test_execute_lock_skip_emits_lock_stuck_at_threshold(tmp_path: Path, caplog) -> None:
    """When skip count reaches threshold, lock_stuck should be emitted."""
    runner = _runner_script(tmp_path, """raise SystemExit(2)""")
    backend = InMemoryLockBackend()
    backend.write_lock("proj-1", "other-holder", ttl=1800)
    manager = ExperimentLockManager(backend=backend)

    # Pre-increment the skip count to (threshold - 1) so the next record_skip triggers.
    for _ in range(9):
        manager.record_skip(project_id="proj-1", experiment_id="exp-1")

    caplog.set_level("WARNING", logger="map.experiment_lock")
    client = LockAwareExperimentFakeClient(
        todos={"my_open_experiments": [{"id": "exp-1"}], "pending_topic_replies": []},
        experiments={
            "exp-1": {
                "id": "exp-1",
                "title": "stuck",
                "phase": "running",
                "project_id": "proj-1",
                "open_unreasonable_count": 0,
                "current_plan_version": 1,
                "current_plan": {"content_md": "## plan"},
                "reviews": [],
            }
        },
        lock_backend=backend,
    )
    HostWorker(
        client,
        WorkerConfig(
            once=True,
            auto_experiment_lifecycle=True,
            agent_runner=runner,
            state_file=tmp_path / "host-state.json",
            plan_dir=tmp_path,
        ),
    ).run_once()

    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert LOG_STUCK in joined
    # And the lock_skip_count should now be at or above threshold.
    detail = client._experiments["exp-1"]
    assert detail["lock_skip_count"] >= 10


def test_execute_lock_release_in_finally_on_error(tmp_path: Path) -> None:
    """If the agent runner errors out, the lock must still be released."""
    runner = _runner_script(
        tmp_path,
        """import json, sys; sys.stdin.readline(); sys.exit(3)""",
    )
    backend = InMemoryLockBackend()
    client = LockAwareExperimentFakeClient(
        todos={"my_open_experiments": [{"id": "exp-1"}], "pending_topic_replies": []},
        experiments={
            "exp-1": {
                "id": "exp-1",
                "title": "error path",
                "phase": "running",
                "project_id": "proj-1",
                "open_unreasonable_count": 0,
                "current_plan_version": 1,
                "current_plan": {"content_md": "## plan"},
                "reviews": [],
            }
        },
        lock_backend=backend,
    )
    HostWorker(
        client,
        WorkerConfig(
            once=True,
            auto_experiment_lifecycle=True,
            agent_runner=runner,
            state_file=tmp_path / "host-state.json",
            plan_dir=tmp_path,
        ),
    ).run_once()

    # Lock must be released even though the runner exited 3.
    state = backend.get_lock_state("proj-1")
    assert state.lock_holder_experiment_id is None


def test_execute_lock_busy_log_payload(tmp_path: Path, caplog) -> None:
    runner = _runner_script(tmp_path, """raise SystemExit(2)""")
    backend = InMemoryLockBackend()
    backend.write_lock("proj-1", "other-holder", ttl=1800)

    caplog.set_level("INFO", logger="map.experiment_lock")
    client = LockAwareExperimentFakeClient(
        todos={"my_open_experiments": [{"id": "exp-1"}], "pending_topic_replies": []},
        experiments={
            "exp-1": {
                "id": "exp-1",
                "title": "busy",
                "phase": "running",
                "project_id": "proj-1",
                "open_unreasonable_count": 0,
                "current_plan_version": 1,
                "current_plan": {"content_md": "## plan"},
                "reviews": [],
            }
        },
        lock_backend=backend,
    )
    HostWorker(
        client,
        WorkerConfig(
            once=True,
            auto_experiment_lifecycle=True,
            agent_runner=runner,
            state_file=tmp_path / "host-state.json",
            plan_dir=tmp_path,
        ),
    ).run_once()

    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert LOG_BUSY in joined
