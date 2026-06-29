from pathlib import Path
from typing import Any

import yaml

from cli.reviewer_worker import ReviewerMapClient, ReviewerWorker, ReviewerWorkerConfig


class FakeReviewerClient(ReviewerMapClient):
    def __init__(
        self,
        *,
        agent_id: str = "reviewer-agent",
        todos: dict[str, Any] | None = None,
        experiments: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._todos = todos or {}
        self._experiments = experiments or {}
        self.reviews: list[dict[str, Any]] = []

    def whoami(self) -> dict[str, Any]:
        return {"id": self._agent_id, "name": "reviewer"}

    def todos(self) -> dict[str, Any]:
        return self._todos

    def experiment_status(self, experiment_id: str) -> dict[str, Any]:
        return self._experiments[experiment_id]

    def review_add(self, experiment_id: str, review_file: Path) -> dict[str, Any]:
        payload = yaml.safe_load(review_file.read_text(encoding="utf-8"))
        review = {"experiment_id": experiment_id, **payload}
        self.reviews.append(review)
        return review


def test_reviewer_submits_review_for_pending(tmp_path):
    review_dir = tmp_path / "reviews"
    client = FakeReviewerClient(
        todos={
            "pending_reviews": [
                {"id": "exp-1", "title": "测试实验", "phase": "review", "current_plan_version": 1}
            ]
        },
        experiments={
            "exp-1": {
                "id": "exp-1",
                "title": "测试实验",
                "phase": "review",
                "current_plan_version": 1,
                "current_plan": {"content_md": "# Plan\n\n## 目标\n\n验证评审"},
            }
        },
    )
    stats = ReviewerWorker(
        client,
        ReviewerWorkerConfig(once=True, review_dir=review_dir, state_file=tmp_path / "state.json"),
    ).run_once()

    assert stats.reviews_created == 1
    assert client.reviews[0]["reasonable_items"]
    assert client.reviews[0]["unreasonable_items"]


def test_reviewer_skips_already_handled(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.write_text(
        '{"schema_version":1,"experiments":{"exp-1":{"last_handled_trigger_id":"exp-1:v1"}}}',
        encoding="utf-8",
    )
    client = FakeReviewerClient(
        todos={"pending_reviews": [{"id": "exp-1", "current_plan_version": 1}]},
        experiments={"exp-1": {"id": "exp-1", "phase": "review", "current_plan_version": 1}},
    )
    stats = ReviewerWorker(
        client,
        ReviewerWorkerConfig(once=True, state_file=state_file),
    ).run_once()
    assert stats.reviews_created == 0
    assert stats.runner_skips == 1
