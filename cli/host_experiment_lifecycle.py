"""Host experiment lifecycle helpers (review → approve → start → execute → complete)."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from cli.experiment_lock import (
    ExperimentLockError,
    ExperimentLockManager,
    InMemoryLockBackend,
    LockBusy,
    LOG_BUSY,
    LOG_STUCK,
    LOG_TIMEOUT,
    MapClientLockBackend,
    env_lock_disabled,
)
from cli.git_checkpoint import GitCheckpointError, checkpoint_after, checkpoint_before

if TYPE_CHECKING:
    from cli.host_worker import HostWorker, WorkerStats


def _build_lock_manager(worker: HostWorker) -> ExperimentLockManager:
    """Construct an :class:`ExperimentLockManager` for the current worker.

    When the client is the production ``MapCommandClient`` we wrap it with
    ``MapClientLockBackend``; otherwise (tests / mocks) we fall back to an
    in-memory backend so unit tests don't need a live API.
    """

    try:
        from cli.map_command_client import MapCommandClient  # local import to avoid cycle

        backend: Any = (
            MapClientLockBackend(worker.client)
            if isinstance(worker.client, MapCommandClient)
            else InMemoryLockBackend()
        )
    except ImportError:  # pragma: no cover
        backend = InMemoryLockBackend()
    manager = ExperimentLockManager.from_env(backend)
    manager.dry_run = manager.dry_run or worker.config.dry_run
    manager.disabled = manager.disabled or env_lock_disabled()
    return manager


def _parse_next_attempt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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
    if phase == "draft":
        return _submit_experiment_for_review(worker, experiment_id, stats)
    if phase == "review":
        reviews = worker.client.experiment_reviews_list(experiment_id) or []
        if not reviews:
            # No reviewer has touched this experiment yet. Wait — approving
            # now would race the reviewer bridge (which needs ~30-60s to
            # generate a review) and bypass the review gate entirely.
            stats.runner_skips += 1
            worker._log_experiment_event(
                "await_reviewer", experiment_id,
                status="no_reviews_yet",
                open_unreasonable_count=int(detail.get("open_unreasonable_count") or 0),
            )
            return False
        open_count = int(detail.get("open_unreasonable_count") or 0)
        if open_count > 0:
            return _revise_plan_for_review(worker, experiment_id, detail, stats)
        return _approve_experiment(worker, experiment_id, detail, stats)
    if phase == "approved":
        return _start_experiment(worker, experiment_id, stats)
    if phase == "running":
        return _execute_and_complete(worker, experiment_id, detail, stats)
    return False


def _submit_experiment_for_review(
    worker: HostWorker,
    experiment_id: str,
    stats: WorkerStats,
) -> bool:
    if worker._experiment_state(experiment_id).get("submitted_for_review"):
        stats.runner_skips += 1
        return False
    if worker.config.dry_run:
        typer.echo(f"[dry-run] would submit experiment={experiment_id} for review")
        stats.dry_run_actions += 1
        return True
    worker.client.experiment_submit_review(experiment_id)
    stats.experiments_submitted += 1
    worker._mark_experiment_state(experiment_id, submitted_for_review=True)
    worker._log_experiment_event("submit_experiment", experiment_id)
    return True


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

    project_id = str(detail.get("project_id") or "") or None
    if not project_id:
        # Fallback: read from status; should never trigger in production.
        try:
            full = worker.client.experiment_status(experiment_id)
            project_id = str(full.get("project_id") or "") or None
        except Exception:  # pragma: no cover - defensive
            project_id = None

    # --- Skip closed-loop: respect next_attempt_at ---------------------------------
    next_attempt_raw = detail.get("next_attempt_at")
    next_attempt_dt = _parse_next_attempt(next_attempt_raw) if isinstance(next_attempt_raw, str) else None
    if next_attempt_dt is not None and next_attempt_dt > datetime.now(UTC):
        worker._log_experiment_event(
            "execute_experiment",
            experiment_id,
            status="lock_skip_deferred",
            next_attempt_at=next_attempt_raw,
        )
        stats.lock_skipped += 1
        return False

    # --- Acquire execution lock --------------------------------------------------
    manager = _build_lock_manager(worker)
    lock_state = None
    try:
        if project_id is None:
            lock_state = None  # type: ignore[assignment]
        else:
            lock_state = manager.acquire(project_id=project_id, experiment_id=experiment_id)
            stats.lock_acquired += 1
    except LockBusy as exc:
        # Closed-loop: increment skip count, compute backoff, do not crash worker.
        if project_id is not None:
            try:
                skip_count, next_attempt_at = manager.record_skip(
                    project_id=project_id, experiment_id=experiment_id
                )
            except ExperimentLockError as err:  # pragma: no cover - defensive
                worker._log_experiment_event(
                    "execute_experiment",
                    experiment_id,
                    status=LOG_TIMEOUT,
                    project_id=project_id,
                    holder=exc.holder,
                    error=str(err),
                )
                stats.runner_errors += 1
                return False
            worker._log_experiment_event(
                "execute_experiment",
                experiment_id,
                status=LOG_BUSY,
                project_id=project_id,
                holder=exc.holder,
                lock_skip_count=skip_count,
                next_attempt_at=next_attempt_at,
            )
            if skip_count >= manager.config.skip_stuck_threshold:
                worker._log_experiment_event(
                    "execute_experiment",
                    experiment_id,
                    status=LOG_STUCK,
                    project_id=project_id,
                    lock_skip_count=skip_count,
                )
        else:
            worker._log_experiment_event(
                "execute_experiment",
                experiment_id,
                status=LOG_BUSY,
                holder=exc.holder,
            )
        stats.lock_skipped += 1
        return False
    except ExperimentLockError as exc:
        worker._log_experiment_event(
            "execute_experiment",
            experiment_id,
            status=LOG_TIMEOUT,
            error=str(exc),
        )
        stats.runner_errors += 1
        return False

    try:
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
                    "project_id": project_id,
                },
                "plan_md": plan_md,
                "git_checkpoint_before": git_before,
                "idempotency_key": f"{experiment_id}:execute",
                "lock_state": {
                    "holder": lock_state.lock_holder_experiment_id if lock_state else experiment_id,
                    "ttl_seconds": lock_state.lock_ttl_seconds if lock_state else None,
                } if lock_state is not None else None,
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
    finally:
        # Always release the lock, even on error / Ctrl-C.
        if project_id is not None and not manager.disabled:
            try:
                manager.release(project_id=project_id, experiment_id=experiment_id)
            except Exception as exc:  # pragma: no cover - defensive
                worker._log_experiment_event(
                    "execute_experiment",
                    experiment_id,
                    status="release_failed",
                    error=str(exc),
                )


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
