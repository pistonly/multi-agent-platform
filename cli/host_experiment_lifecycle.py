"""Host experiment lifecycle helpers (review → approve → start → execute → complete)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from cli.git_checkpoint import GitCheckpointError, checkpoint_after, checkpoint_before

if TYPE_CHECKING:
    from cli.host_worker import HostWorker, WorkerStats


def manage_experiment_lifecycle(worker: HostWorker, todos: dict[str, Any], stats: WorkerStats) -> None:
    if not worker.config.auto_experiment_lifecycle or not worker.config.agent_runner:
        return

    experiments = list(todos.get("my_open_experiments") or [])
    if not experiments:
        return

    for summary in experiments:
        experiment_id = str(summary.get("id") or "")
        if not experiment_id:
            continue
        detail = worker.client.experiment_status(experiment_id)
        phase = str(detail.get("phase") or "")
        if _handle_experiment_phase(worker, experiment_id, phase, detail, stats):
            break


def _handle_experiment_phase(
    worker: HostWorker,
    experiment_id: str,
    phase: str,
    detail: dict[str, Any],
    stats: WorkerStats,
) -> bool:
    if phase == "review":
        open_count = int(detail.get("open_unreasonable_count") or 0)
        if open_count > 0:
            return _revise_plan_for_review(worker, experiment_id, detail, stats)
        return _approve_experiment(worker, experiment_id, detail, stats)
    if phase == "approved":
        return _start_experiment(worker, experiment_id, stats)
    if phase == "running":
        return _execute_and_complete(worker, experiment_id, detail, stats)
    return False


def _revise_plan_for_review(
    worker: HostWorker,
    experiment_id: str,
    detail: dict[str, Any],
    stats: WorkerStats,
) -> bool:
    plan_version = int(detail.get("current_plan_version") or 0)
    exp_state = worker._experiment_state(experiment_id)
    trigger = f"revise:v{plan_version}"
    if exp_state.get("last_revise_trigger") == trigger:
        stats.runner_skips += 1
        return False

    reviews = worker.client.experiment_reviews_list(experiment_id) or []
    item_ids = _open_unreasonable_item_ids(reviews)
    if not item_ids:
        return False

    unreasonable = _open_unreasonable_items(reviews)
    plan_md = (detail.get("current_plan") or {}).get("content_md") or ""
    request = {
        "action": "revise_plan",
        "experiment_id": experiment_id,
        "dry_run": worker.config.dry_run,
        "context": {
            "experiment": {
                "id": detail.get("id"),
                "title": detail.get("title"),
                "phase": detail.get("phase"),
                "current_plan_version": plan_version,
            },
            "plan_md": plan_md,
            "unreasonable_items": unreasonable,
            "idempotency_key": f"{experiment_id}:{trigger}",
        },
    }
    status, result = worker._invoke_runner(
        request,
        action="revise_plan",
        topic_id=experiment_id,
    )
    stats.runner_invocations += 1
    if status == "error":
        stats.runner_errors += 1
        return False
    if status == "skip":
        stats.runner_skips += 1
        worker._mark_experiment_state(experiment_id, last_revise_trigger=trigger)
        return False

    body = result.get("body")
    if not body or not isinstance(body, str):
        stats.runner_errors += 1
        return False

    if worker.config.dry_run:
        typer.echo(f"[dry-run] would revise plan experiment={experiment_id} items={item_ids}\n{body[:500]}")
        stats.dry_run_actions += 1
        return True

    plan_file = _write_plan_file(worker, experiment_id, body, suffix="-revised.md")
    worker.client.plan_revise(
        experiment_id,
        plan_file,
        note=str(result.get("change_note") or "address review via host bridge"),
        addressed_item_ids=item_ids,
    )
    stats.plans_revised += 1
    worker._mark_experiment_state(experiment_id, last_revise_trigger=trigger)
    return True


def _approve_experiment(worker: HostWorker, experiment_id: str, detail: dict[str, Any], stats: WorkerStats) -> bool:
    if worker._experiment_state(experiment_id).get("approved"):
        stats.runner_skips += 1
        return False
    if int(detail.get("open_unreasonable_count") or 0) > 0:
        return False
    if worker.config.dry_run:
        typer.echo(f"[dry-run] would approve experiment={experiment_id}")
        stats.dry_run_actions += 1
        return True
    worker.client.experiment_approve(experiment_id)
    stats.experiments_approved += 1
    worker._mark_experiment_state(experiment_id, approved=True)
    return True


def _start_experiment(worker: HostWorker, experiment_id: str, stats: WorkerStats) -> bool:
    if worker._experiment_state(experiment_id).get("started"):
        stats.runner_skips += 1
        return False
    if worker.config.dry_run:
        typer.echo(f"[dry-run] would start experiment={experiment_id}")
        stats.dry_run_actions += 1
        return True
    worker.client.experiment_start(experiment_id)
    stats.experiments_started += 1
    worker._mark_experiment_state(experiment_id, started=True)
    return True


def _execute_and_complete(
    worker: HostWorker,
    experiment_id: str,
    detail: dict[str, Any],
    stats: WorkerStats,
) -> bool:
    if worker._experiment_state(experiment_id).get("completed"):
        stats.runner_skips += 1
        return False

    repo = worker._git_repo()
    plan_md = (detail.get("current_plan") or {}).get("content_md") or ""
    git_before: str | None = None
    if repo is not None and not worker.config.dry_run:
        exp_state = worker._experiment_state(experiment_id)
        existing = exp_state.get("git_checkpoint_before")
        if existing:
            git_before = str(existing)
        else:
            try:
                git_before = checkpoint_before(repo, experiment_id)
                worker._mark_experiment_state(experiment_id, git_checkpoint_before=git_before)
            except GitCheckpointError as exc:
                worker._log_experiment_event(
                    "execute_experiment", experiment_id, status="git_before_failed", error=str(exc)
                )
                stats.runner_errors += 1
                return False

    request = {
        "action": "execute_experiment",
        "experiment_id": experiment_id,
        "dry_run": worker.config.dry_run,
        "context": {
            "experiment": {
                "id": detail.get("id"),
                "title": detail.get("title"),
                "phase": detail.get("phase"),
                "topic_id": detail.get("topic_id"),
            },
            "plan_md": plan_md,
            "git_checkpoint_before": git_before,
            "idempotency_key": f"{experiment_id}:execute",
        },
    }
    status, result = worker._invoke_runner(request, action="execute_experiment", topic_id=experiment_id)
    stats.runner_invocations += 1
    if status == "error":
        stats.runner_errors += 1
        return False
    if status == "skip":
        stats.runner_skips += 1
        worker._mark_experiment_state(experiment_id, completed=True)
        return False

    log_body = result.get("execution_log_md") or result.get("body")
    summary = result.get("summary") or f"完成实验 {detail.get('title') or experiment_id}"
    if not log_body or not isinstance(log_body, str):
        stats.runner_errors += 1
        return False

    git_after: str | None = None
    if repo is not None and not worker.config.dry_run:
        try:
            git_after = checkpoint_after(repo, experiment_id, summary)
            if git_after:
                worker._mark_experiment_state(experiment_id, git_checkpoint_after=git_after)
        except GitCheckpointError as exc:
            worker._log_experiment_event("execute_experiment", experiment_id, status="git_after_failed", error=str(exc))
            stats.runner_errors += 1
            return False

    if worker.config.dry_run:
        typer.echo(f"[dry-run] would complete experiment={experiment_id}\n{log_body[:500]}")
        stats.dry_run_actions += 1
        return True

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as fh:
        fh.write(log_body)
        fh.flush()
        log_file = Path(fh.name)
    worker.client.experiment_complete(experiment_id, summary=str(summary), log_file=log_file)
    stats.experiments_completed += 1
    worker._mark_experiment_state(experiment_id, completed=True)
    return True


def _open_unreasonable_item_ids(reviews: list[dict[str, Any]]) -> list[str]:
    return [str(item["id"]) for item in _open_unreasonable_items(reviews)]


def _open_unreasonable_items(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for review in reviews:
        for item in review.get("items") or []:
            if item.get("kind") == "unreasonable" and item.get("status") == "open":
                items.append(
                    {
                        "id": item.get("id"),
                        "content": item.get("content"),
                        "status": item.get("status"),
                    }
                )
    return items


def _write_plan_file(worker: HostWorker, experiment_id: str, body: str, *, suffix: str) -> Path:
    plan_dir = worker.config.plan_dir
    if plan_dir is not None:
        plan_dir.mkdir(parents=True, exist_ok=True)
        path = plan_dir / f"experiment-{experiment_id}{suffix}"
        path.write_text(body, encoding="utf-8")
        return path
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=suffix, delete=False) as fh:
        fh.write(body)
        fh.flush()
        return Path(fh.name)
