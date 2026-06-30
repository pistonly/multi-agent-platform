import sys
from pathlib import Path
from typing import Any

from cli.host_worker import HostWorker, WorkerConfig


class ExperimentFakeClient:
    def __init__(self, *, todos: dict[str, Any], experiments: dict[str, dict[str, Any]]) -> None:
        self._todos = todos
        self._experiments = experiments
        self.revisions: list[dict[str, Any]] = []
        self.approvals: list[str] = []
        self.starts: list[str] = []
        self.completions: list[dict[str, Any]] = []
        self.submits: list[str] = []

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
        return self._experiments[experiment_id]

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


def _runner_script(tmp_path: Path, body: str) -> str:
    path = tmp_path / "runner.py"
    path.write_text(body, encoding="utf-8")
    return f"{sys.executable} {path}"


def test_host_auto_approves_when_review_clear(tmp_path: Path) -> None:
    runner = _runner_script(
        tmp_path,
        """
raise SystemExit(2)
""",
    )
    client = ExperimentFakeClient(
        todos={"my_open_experiments": [{"id": "exp-1"}], "pending_topic_replies": []},
        experiments={
            "exp-1": {
                "id": "exp-1",
                "title": "auto",
                "phase": "review",
                "open_unreasonable_count": 0,
                "current_plan_version": 1,
                "current_plan": {"content_md": "## plan"},
                "reviews": [],
            }
        },
    )
    stats = HostWorker(
        client,
        WorkerConfig(
            once=True,
            auto_experiment_lifecycle=True,
            agent_runner=runner,
            state_file=tmp_path / "host-state.json",
        ),
    ).run_once()

    assert stats.experiments_approved == 1
    assert client.approvals == ["exp-1"]
    assert stats.runner_invocations == 0


def test_host_revises_plan_when_open_unreasonable(tmp_path: Path) -> None:
    runner = _runner_script(
        tmp_path,
        """
import json, sys
req = json.loads(sys.stdin.readline())
assert req["action"] == "revise_plan"
print(json.dumps({"body": "## revised plan", "change_note": "fix review"}))
""",
    )
    client = ExperimentFakeClient(
        todos={"my_open_experiments": [{"id": "exp-1"}]},
        experiments={
            "exp-1": {
                "id": "exp-1",
                "title": "revise me",
                "phase": "review",
                "open_unreasonable_count": 1,
                "current_plan_version": 1,
                "current_plan": {"content_md": "## old"},
                "reviews": [
                    {
                        "items": [
                            {
                                "id": "item-1",
                                "kind": "unreasonable",
                                "status": "open",
                                "content": "need detail",
                            }
                        ]
                    }
                ],
            }
        },
    )
    stats = HostWorker(
        client,
        WorkerConfig(
            once=True,
            auto_experiment_lifecycle=True,
            agent_runner=runner,
            plan_dir=tmp_path,
            state_file=tmp_path / "host-state.json",
        ),
    ).run_once()

    assert stats.plans_revised == 1
    assert client.revisions[0]["addressed_item_ids"] == ["item-1"]
    assert "## revised plan" in client.revisions[0]["plan"]


def test_host_submits_draft_experiment_for_review(tmp_path: Path) -> None:
    """A draft experiment (created without --submit-for-review) must be
    submitted for review on the next cycle so the reviewer bridge can pick
    it up. Regression test for the case where the host CLI forgot
    MAP_HOST_SUBMIT_REVIEW=1 — bridges used to sit idle on draft forever.
    """
    runner = _runner_script(
        tmp_path,
        """
raise SystemExit(2)
""",
    )
    client = ExperimentFakeClient(
        todos={"my_open_experiments": [{"id": "exp-1"}]},
        experiments={
            "exp-1": {
                "id": "exp-1",
                "title": "draft experiment",
                "phase": "draft",
                "current_plan_version": 1,
                "current_plan": {"content_md": "## plan"},
                "reviews": [],
            }
        },
    )
    state_file = tmp_path / "host-state.json"
    worker = HostWorker(
        client,
        WorkerConfig(
            once=True,
            auto_experiment_lifecycle=True,
            agent_runner=runner,
            state_file=state_file,
        ),
    )
    stats = worker.run_once()

    assert stats.experiments_submitted == 1
    assert client.submits == ["exp-1"]
    # Subsequent cycles must not re-submit — idempotent via worker state.
    second = worker.run_once()
    assert second.experiments_submitted == 0
    assert client.submits == ["exp-1"]
