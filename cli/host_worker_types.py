from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

DEFAULT_REPLY_TEMPLATE = """已收到这个 thread 的反馈。

- 评论摘要：{excerpt}
- 处理状态：已纳入主持跟进；如需进入实验，我会在本话题完成必要讨论后从 host persona 创建关联实验。
"""


class WorkerError(RuntimeError):
    pass


class MapClientProtocol(Protocol):
    def whoami(self) -> dict[str, Any]:
        ...

    def todos(self) -> dict[str, Any]:
        ...

    def topic_show(self, topic_id: str) -> dict[str, Any]:
        ...

    def topic_comment(self, topic_id: str, body: str, parent_id: str | None = None) -> dict[str, Any] | None:
        ...

    def topic_advance_round(self, topic_id: str) -> dict[str, Any] | None:
        ...

    def topic_resolve(self, topic_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        ...

    def experiment_create(
        self,
        title: str,
        plan_file: Path,
        *,
        topic_id: str,
        submit_for_review: bool,
    ) -> dict[str, Any] | None:
        ...

    def experiment_status(self, experiment_id: str) -> dict[str, Any]:
        ...

    def experiment_submit_review(self, experiment_id: str) -> dict[str, Any] | None:
        ...

    def experiment_reviews_list(self, experiment_id: str) -> list[dict[str, Any]]:
        ...

    def plan_revise(
        self,
        experiment_id: str,
        plan_file: Path,
        *,
        note: str | None,
        addressed_item_ids: list[str],
    ) -> dict[str, Any] | None:
        ...

    def experiment_approve(self, experiment_id: str) -> dict[str, Any] | None:
        ...

    def experiment_start(self, experiment_id: str) -> dict[str, Any] | None:
        ...

    def experiment_complete(self, experiment_id: str, *, summary: str, log_file: Path) -> dict[str, Any] | None:
        ...

    def experiment_acquire_lock(self, experiment_id: str, *, ttl_seconds: int) -> dict[str, Any] | None:
        ...

    def experiment_release_lock(self, experiment_id: str) -> dict[str, Any] | None:
        ...

    def experiment_force_release_lock(
        self, experiment_id: str, *, reason: str, actor: str | None = None
    ) -> dict[str, Any] | None:
        ...

    def experiment_record_skip(
        self, experiment_id: str, *, next_attempt_at: str
    ) -> dict[str, Any] | None:
        ...


@dataclass
class WorkerConfig:
    interval: float = 30.0
    once: bool = False
    max_cycles: int | None = None
    dry_run: bool = False
    reply_template: str = DEFAULT_REPLY_TEMPLATE
    manage_topic_lifecycle: bool = True
    promote_ready_topics: bool = False
    submit_created_experiment_for_review: bool = False
    min_participant_comments: int = 1
    min_round_summaries: int = 2
    max_lifecycle_actions_per_cycle: int = 1
    auto_experiment_lifecycle: bool = True
    plan_dir: Path | None = None
    agent_runner: str | None = None
    runner_timeout: float = 120.0
    state_file: Path | None = Path(".map/host-bridge-state.json")


@dataclass
class WorkerStats:
    cycles: int = 0
    replies_created: int = 0
    summaries_created: int = 0
    decisions_recorded: int = 0
    experiments_created: int = 0
    plans_revised: int = 0
    experiments_submitted: int = 0
    experiments_approved: int = 0
    experiments_started: int = 0
    experiments_completed: int = 0
    dry_run_actions: int = 0
    runner_invocations: int = 0
    runner_skips: int = 0
    runner_errors: int = 0
    round_advances: int = 0
    lock_acquired: int = 0
    lock_skipped: int = 0
    lock_force_released: int = 0

    def add(self, other: "WorkerStats") -> None:
        self.cycles += other.cycles
        self.replies_created += other.replies_created
        self.summaries_created += other.summaries_created
        self.decisions_recorded += other.decisions_recorded
        self.experiments_created += other.experiments_created
        self.plans_revised += other.plans_revised
        self.experiments_submitted += other.experiments_submitted
        self.experiments_approved += other.experiments_approved
        self.experiments_started += other.experiments_started
        self.experiments_completed += other.experiments_completed
        self.dry_run_actions += other.dry_run_actions
        self.runner_invocations += other.runner_invocations
        self.runner_skips += other.runner_skips
        self.runner_errors += other.runner_errors
        self.round_advances += other.round_advances
        self.lock_acquired += other.lock_acquired
        self.lock_skipped += other.lock_skipped
        self.lock_force_released += other.lock_force_released
