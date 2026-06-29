from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
import yaml

from cli.host_worker import MapCommandClient, WorkerError

DEFAULT_REASONABLE = ["实验计划结构完整，目标与步骤可辨识。"]
DEFAULT_UNREASONABLE = ["建议补充更具体的验收标准与可观测结果。"]


class ReviewerMapClient(MapCommandClient):
    def experiment_status(self, experiment_id: str) -> dict[str, Any]:
        return self._run(["experiment", "status", "--id", experiment_id])

    def review_add(self, experiment_id: str, review_file: Path) -> dict[str, Any] | None:
        return self._run(["experiment", "review", "add", "--id", experiment_id, "--review", str(review_file)])


@dataclass
class ReviewerWorkerConfig:
    interval: float = 30.0
    once: bool = False
    max_cycles: int | None = None
    dry_run: bool = False
    max_reviews_per_cycle: int = 1
    agent_runner: str | None = None
    runner_timeout: float = 180.0
    state_file: Path | None = Path(".map/reviewer-bridge-state.json")
    review_dir: Path | None = Path(".map/generated-reviews")


@dataclass
class ReviewerWorkerStats:
    cycles: int = 0
    reviews_created: int = 0
    dry_run_actions: int = 0
    runner_invocations: int = 0
    runner_skips: int = 0
    runner_errors: int = 0
    pending_seen: int = 0


@dataclass
class ReviewerWorker:
    client: ReviewerMapClient
    config: ReviewerWorkerConfig = field(default_factory=ReviewerWorkerConfig)
    agent_id: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    _state_dirty: bool = False

    def __post_init__(self) -> None:
        if not self.state:
            self.state = _load_state(self.config.state_file)

    def run_forever(self) -> ReviewerWorkerStats:
        total = ReviewerWorkerStats()
        while True:
            stats = self.run_once()
            total.cycles += stats.cycles
            total.reviews_created += stats.reviews_created
            total.dry_run_actions += stats.dry_run_actions
            total.runner_invocations += stats.runner_invocations
            total.runner_skips += stats.runner_skips
            total.runner_errors += stats.runner_errors
            total.pending_seen += stats.pending_seen

            if self.config.once:
                break
            if self.config.max_cycles is not None and total.cycles >= self.config.max_cycles:
                break
            time.sleep(self.config.interval)
        return total

    def run_once(self) -> ReviewerWorkerStats:
        self._ensure_identity()
        stats = ReviewerWorkerStats(cycles=1)
        todos = self.client.todos() or {}
        pending = list(todos.get("pending_reviews") or [])
        stats.pending_seen = len(pending)

        limit = max(1, self.config.max_reviews_per_cycle)
        for item in pending[:limit]:
            experiment_id = str(item.get("id") or "")
            if not experiment_id:
                continue
            plan_version = int(item.get("current_plan_version") or 0)
            trigger_id = f"{experiment_id}:v{plan_version}"
            if self._already_handled(experiment_id, trigger_id):
                stats.runner_skips += 1
                continue

            detail = self.client.experiment_status(experiment_id)
            if str(detail.get("phase")) != "review":
                continue

            if self.config.agent_runner:
                self._handle_with_runner(experiment_id, trigger_id, detail, stats)
            else:
                self._handle_with_template(experiment_id, trigger_id, detail, stats)

        self._save_state_if_needed()
        return stats

    def _ensure_identity(self) -> None:
        if self.agent_id is not None:
            return
        me = self.client.whoami()
        if not me or not me.get("id"):
            raise WorkerError("Could not resolve reviewer identity; run `map --persona reviewer persona whoami`")
        self.agent_id = str(me["id"])

    def _already_handled(self, experiment_id: str, trigger_id: str) -> bool:
        experiments = self.state.setdefault("experiments", {})
        if not isinstance(experiments, dict):
            return False
        exp_state = experiments.get(experiment_id) or {}
        return exp_state.get("last_handled_trigger_id") == trigger_id

    def _handle_with_template(
        self,
        experiment_id: str,
        trigger_id: str,
        detail: dict[str, Any],
        stats: ReviewerWorkerStats,
    ) -> None:
        payload = {
            "reasonable_items": list(DEFAULT_REASONABLE),
            "unreasonable_items": list(DEFAULT_UNREASONABLE),
        }
        self._submit_review(experiment_id, trigger_id, detail, payload, stats)

    def _handle_with_runner(
        self,
        experiment_id: str,
        trigger_id: str,
        detail: dict[str, Any],
        stats: ReviewerWorkerStats,
    ) -> None:
        plan = detail.get("current_plan") or {}
        request = {
            "action": "review_experiment",
            "experiment_id": experiment_id,
            "dry_run": self.config.dry_run,
            "context": {
                "experiment": {
                    "id": detail.get("id"),
                    "title": detail.get("title"),
                    "phase": detail.get("phase"),
                    "current_plan_version": detail.get("current_plan_version"),
                    "topic_id": detail.get("topic_id"),
                },
                "plan_md": plan.get("content_md") or "",
                "idempotency_key": trigger_id,
            },
        }
        status, result = self._invoke_runner(request, experiment_id=experiment_id)
        stats.runner_invocations += 1
        if status == "error":
            stats.runner_errors += 1
            return
        if status == "skip":
            stats.runner_skips += 1
            self._mark_handled(experiment_id, trigger_id)
            return

        reasonable = result.get("reasonable_items")
        unreasonable = result.get("unreasonable_items")
        if not isinstance(reasonable, list) or not isinstance(unreasonable, list):
            stats.runner_errors += 1
            self._log_event("review_experiment", experiment_id, status="runner_invalid_items")
            return

        payload = {
            "reasonable_items": [str(x) for x in reasonable],
            "unreasonable_items": [str(x) for x in unreasonable],
        }
        self._submit_review(experiment_id, trigger_id, detail, payload, stats)

    def _submit_review(
        self,
        experiment_id: str,
        trigger_id: str,
        detail: dict[str, Any],
        payload: dict[str, list[str]],
        stats: ReviewerWorkerStats,
    ) -> None:
        title = detail.get("title") or experiment_id
        if self.config.dry_run:
            typer.echo(
                f"[dry-run] would review experiment={experiment_id} title={title}\n"
                f"{yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)}"
            )
            stats.dry_run_actions += 1
            return

        review_file = self._write_review_file(experiment_id, payload)
        self.client.review_add(experiment_id, review_file)
        stats.reviews_created += 1
        self._mark_handled(experiment_id, trigger_id)

    def _write_review_file(self, experiment_id: str, payload: dict[str, list[str]]) -> Path:
        review_dir = self.config.review_dir
        if review_dir is not None:
            review_dir.mkdir(parents=True, exist_ok=True)
            path = review_dir / f"experiment-{experiment_id}-review.yaml"
            path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
            return path

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".yaml", delete=False) as fh:
            fh.write(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
            fh.flush()
            return Path(fh.name)

    def _invoke_runner(self, request: dict[str, Any], *, experiment_id: str) -> tuple[str, dict[str, Any]]:
        if not self.config.agent_runner:
            raise WorkerError("agent_runner is not configured")
        cmd = shlex.split(self.config.agent_runner)
        payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            result = subprocess.run(
                cmd,
                input=payload,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.config.runner_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self._log_event("review_experiment", experiment_id, status="runner_timeout")
            return "error", {}

        if result.returncode == 2:
            self._log_event("review_experiment", experiment_id, status="runner_skip", stderr=result.stderr.strip())
            return "skip", {}
        if result.returncode != 0:
            self._log_event(
                "review_experiment",
                experiment_id,
                status="runner_error",
                exit_code=result.returncode,
                stderr=result.stderr.strip(),
            )
            return "error", {}

        try:
            parsed = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            self._log_event("review_experiment", experiment_id, status="runner_invalid_json")
            return "error", {}
        if not isinstance(parsed, dict):
            return "error", {}
        return "ok", parsed

    def _mark_handled(self, experiment_id: str, trigger_id: str) -> None:
        if self.config.dry_run:
            return
        experiments = self.state.setdefault("experiments", {})
        if not isinstance(experiments, dict):
            experiments = {}
            self.state["experiments"] = experiments
        experiments[experiment_id] = {
            "last_handled_trigger_id": trigger_id,
            "last_action_at": datetime.now(UTC).isoformat(),
        }
        self._state_dirty = True

    def _save_state_if_needed(self) -> None:
        if self.config.dry_run or not self._state_dirty:
            return
        _save_state(self.config.state_file, self.state)
        self._state_dirty = False

    def _log_event(self, action: str, experiment_id: str, **fields: Any) -> None:
        event = {
            "action": action,
            "experiment_id": experiment_id,
            "dry_run": self.config.dry_run,
            **{key: value for key, value in fields.items() if value not in (None, "")},
        }
        typer.echo(json.dumps(event, ensure_ascii=False, sort_keys=True), err=True)


def _load_state(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"schema_version": 1, "experiments": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkerError(f"Invalid reviewer bridge state file: {path}") from exc
    if not isinstance(state, dict):
        raise WorkerError(f"Invalid reviewer bridge state file: {path}")
    state.setdefault("schema_version", 1)
    state.setdefault("experiments", {})
    return state


def _save_state(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def run(
    persona: str = typer.Option("reviewer", "--persona", "-p"),
    project_root: Path | None = typer.Option(None, "--project-root"),
    map_cmd: str = typer.Option("map", "--map-cmd"),
    interval: float = typer.Option(30.0, "--interval", min=1.0),
    once: bool = typer.Option(False, "--once"),
    max_cycles: int | None = typer.Option(None, "--max-cycles", min=1),
    dry_run: bool = typer.Option(False, "--dry-run"),
    max_reviews_per_cycle: int = typer.Option(1, "--max-reviews-per-cycle", min=1),
    agent_runner: str | None = typer.Option(None, "--agent-runner"),
    runner_timeout: float = typer.Option(180.0, "--runner-timeout", min=1.0),
    state_file: Path | None = typer.Option(Path(".map/reviewer-bridge-state.json"), "--state-file"),
    review_dir: Path | None = typer.Option(Path(".map/generated-reviews"), "--review-dir"),
) -> None:
    config = ReviewerWorkerConfig(
        interval=interval,
        once=once,
        max_cycles=max_cycles,
        dry_run=dry_run,
        max_reviews_per_cycle=max_reviews_per_cycle,
        agent_runner=agent_runner,
        runner_timeout=runner_timeout,
        state_file=state_file,
        review_dir=review_dir,
    )
    client = ReviewerMapClient(
        map_cmd=map_cmd,
        persona=persona,
        project_root=project_root,
        dry_run=dry_run,
    )
    stats = ReviewerWorker(client, config).run_forever()
    typer.echo(yaml.safe_dump(stats.__dict__, allow_unicode=True, sort_keys=False))


def main() -> None:
    typer.run(run)


if __name__ == "__main__":
    main()
