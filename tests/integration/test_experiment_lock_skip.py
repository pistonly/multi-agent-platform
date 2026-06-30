"""Integration: skip closed-loop — backoff advances and lock_stuck triggers."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from cli.host_worker import HostWorker, WorkerConfig
from cli.experiment_lock import (
    LOG_STUCK,
    InMemoryLockBackend,
)


class _SkipClient:
    def __init__(
        self,
        *,
        backend: InMemoryLockBackend,
        experiments: dict[str, dict[str, Any]],
    ) -> None:
        self.backend = backend
        self.experiments = experiments
        self.completions: list[str] = []
        self.events: list[dict[str, Any]] = []

    def whoami(self) -> dict[str, Any]:
        return {"id": "host"}

    def todos(self) -> dict[str, Any]:
        return {"my_open_experiments": [{"id": eid} for eid in self.experiments], "pending_topic_replies": []}

    def topic_show(self, topic_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def topic_comment(self, topic_id: str, body: str, parent_id: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def topic_advance_round(self, topic_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def experiment_create(self, title: str, plan_file: Path, *, topic_id: str, submit_for_review: bool) -> dict:
        raise NotImplementedError

    def experiment_status(self, experiment_id: str) -> dict[str, Any]:
        detail = dict(self.experiments[experiment_id])
        project_id = str(detail.get("project_id") or "")
        state = self.backend.get_lock_state(project_id)
        if state.is_held:
            detail["lock_holder_experiment_id"] = state.lock_holder_experiment_id
            detail["lock_acquired_at"] = state.lock_acquired_at
            detail["lock_ttl_seconds"] = state.lock_ttl_seconds
        detail.setdefault("lock_skip_count", 0)
        if state.next_attempt_at:
            detail["next_attempt_at"] = state.next_attempt_at
        return detail

    def experiment_submit_review(self, experiment_id: str) -> dict[str, Any]:
        return {"id": experiment_id}

    def experiment_reviews_list(self, experiment_id: str) -> list[dict[str, Any]]:
        return []

    def plan_revise(self, experiment_id, plan_file, *, note, addressed_item_ids):
        return {"version": 2}

    def experiment_approve(self, experiment_id) -> dict[str, Any]:
        return {"id": experiment_id}

    def experiment_start(self, experiment_id) -> dict[str, Any]:
        self.experiments[experiment_id]["phase"] = "running"
        return {"id": experiment_id}

    def experiment_complete(self, experiment_id, *, summary, log_file) -> dict[str, Any]:
        self.completions.append(experiment_id)
        self.experiments[experiment_id]["phase"] = "done"
        return {"id": experiment_id}

    def experiment_acquire_lock(self, experiment_id, *, ttl_seconds):
        detail = self.experiments[experiment_id]
        project_id = str(detail.get("project_id") or "")
        self.backend.write_lock(project_id, experiment_id, ttl=ttl_seconds)
        return {"id": experiment_id}

    def experiment_release_lock(self, experiment_id):
        detail = self.experiments[experiment_id]
        project_id = str(detail.get("project_id") or "")
        self.backend.clear_lock(project_id, experiment_id=experiment_id)
        return {"id": experiment_id}

    def experiment_force_release_lock(self, experiment_id, *, reason, actor=None):
        detail = self.experiments[experiment_id]
        project_id = str(detail.get("project_id") or "")
        self.backend.clear_lock(project_id)
        return {"id": experiment_id}

    def experiment_record_skip(self, experiment_id, *, next_attempt_at):
        detail = self.experiments[experiment_id]
        project_id = str(detail.get("project_id") or "")
        state = self.backend.bump_skip_count(project_id, experiment_id, next_attempt_at=next_attempt_at)
        detail["lock_skip_count"] = state.lock_skip_count
        detail["next_attempt_at"] = state.next_attempt_at
        return {"id": experiment_id, "lock_skip_count": state.lock_skip_count}


def _runner_script(tmp_path: Path, body: str) -> str:
    path = tmp_path / "runner.py"
    path.write_text(body, encoding="utf-8")
    return f"{sys.executable} {path}"


def test_lock_skip_count_advances_backoff(tmp_path: Path) -> None:
    """Each skipped attempt bumps lock_skip_count and pushes next_attempt_at."""
    backend = InMemoryLockBackend()
    backend.write_lock("proj-1", "other", ttl=1800)

    client = _SkipClient(
        backend=backend,
        experiments={
            "exp-1": {
                "id": "exp-1",
                "title": "t",
                "phase": "running",
                "project_id": "proj-1",
                "open_unreasonable_count": 0,
                "current_plan_version": 1,
                "current_plan": {"content_md": "## plan"},
                "reviews": [],
                "lock_skip_count": 0,
            }
        },
    )
    runner = _runner_script(tmp_path, "raise SystemExit(2)")

    worker = HostWorker(
        client,
        WorkerConfig(
            once=True,
            auto_experiment_lifecycle=True,
            agent_runner=runner,
            state_file=tmp_path / "host-state.json",
            plan_dir=tmp_path,
        ),
    )
    # Run several cycles; each should bump skip count.
    for _ in range(3):
        worker.run_once()

    detail = client.experiments["exp-1"]
    assert detail["lock_skip_count"] >= 3
    assert detail.get("next_attempt_at")
    # other still holds the lock
    state = backend.get_lock_state("proj-1")
    assert state.lock_holder_experiment_id == "other"


def test_lock_stuck_emitted_after_threshold(tmp_path: Path, caplog) -> None:
    backend = InMemoryLockBackend()
    backend.write_lock("proj-1", "other", ttl=1800)

    client = _SkipClient(
        backend=backend,
        experiments={
            "exp-1": {
                "id": "exp-1",
                "title": "t",
                "phase": "running",
                "project_id": "proj-1",
                "open_unreasonable_count": 0,
                "current_plan_version": 1,
                "current_plan": {"content_md": "## plan"},
                "reviews": [],
                "lock_skip_count": 9,  # one more skip triggers lock_stuck
            }
        },
    )
    runner = _runner_script(tmp_path, "raise SystemExit(2)")
    caplog.set_level("WARNING", logger="map.experiment_lock")

    worker = HostWorker(
        client,
        WorkerConfig(
            once=True,
            auto_experiment_lifecycle=True,
            agent_runner=runner,
            state_file=tmp_path / "host-state.json",
            plan_dir=tmp_path,
        ),
    )
    worker.run_once()

    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert LOG_STUCK in joined
    assert client.experiments["exp-1"]["lock_skip_count"] >= 10
