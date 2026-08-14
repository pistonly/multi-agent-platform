"""Per-project experiment execution lock service (CP-3 server side).

The lock is **soft**: a row-level lock stored on the ``experiments`` table
itself (``lock_holder_experiment_id`` + ``lock_acquired_at`` + ``lock_ttl_seconds``)
is the source of truth. Stale locks self-heal via ``lock_ttl_seconds``. The
CLI side (``cli/runtime/run_lock.py``) adds a best-effort ``fcntl.flock`` for
intra-host safety; the server side is authoritative.

Endpoints wired in :mod:`server.api.experiments`:

* ``POST /experiments/{id}/lock/acquire``  → :func:`acquire_experiment_lock`
* ``POST /experiments/{id}/lock/release``  → :func:`release_experiment_lock`
* ``POST /experiments/{id}/lock/force-release`` → :func:`force_release_experiment_lock`
* ``POST /experiments/{id}/lock/skip``     → :func:`record_experiment_lock_skip`
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.db.deadlock_retry import commit_with_retry
from server.domain.models import Agent, Experiment
from server.services.errors import ConflictError, ForbiddenError, NotFoundError

logger = logging.getLogger("map.experiment_lock")

DEFAULT_LOCK_TTL_SECONDS = 1800


@dataclass(frozen=True)
class LockResult:
    """Compact representation of a successful lock operation.

    Returned to the API caller and also persisted on the experiment row.
    """

    experiment_id: uuid.UUID
    project_id: uuid.UUID
    holder: uuid.UUID | None
    acquired_at: datetime | None
    ttl_seconds: int | None
    next_attempt_at: datetime | None
    skip_count: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(experiment: Experiment, *, now: datetime | None = None) -> bool:
    if not experiment.lock_holder_experiment_id or not experiment.lock_acquired_at:
        return True
    reference = now or _now()
    acquired = experiment.lock_acquired_at
    if acquired.tzinfo is None:
        acquired = acquired.replace(tzinfo=timezone.utc)
    ttl = experiment.lock_ttl_seconds or DEFAULT_LOCK_TTL_SECONDS
    return reference >= acquired + __import__("datetime").timedelta(seconds=ttl)


def _to_result(experiment: Experiment) -> LockResult:
    return LockResult(
        experiment_id=experiment.id,
        project_id=experiment.project_id,
        holder=experiment.lock_holder_experiment_id,
        acquired_at=experiment.lock_acquired_at,
        ttl_seconds=experiment.lock_ttl_seconds,
        next_attempt_at=experiment.next_attempt_at,
        skip_count=int(experiment.lock_skip_count or 0),
    )


def _get(db: Session, experiment_id: uuid.UUID) -> Experiment:
    stmt = select(Experiment).where(Experiment.id == experiment_id)
    experiment = db.scalar(stmt)
    if experiment is None or experiment.deleted_at is not None:
        raise NotFoundError(f"experiment {experiment_id} not found")
    return experiment


def _ensure_can_modify_lock(actor: Agent, experiment: Experiment) -> None:
    if experiment.creator_agent_id != actor.id and actor.role.value != "admin":
        raise ForbiddenError("Only the creator or admin can manage the experiment lock")


def _find_project_holder(db: Session, project_id: uuid.UUID, *, exclude_id: uuid.UUID | None = None) -> Experiment | None:
    """Find an experiment in ``project_id`` that currently holds the lock."""

    stmt = (
        select(Experiment)
        .where(
            Experiment.project_id == project_id,
            Experiment.lock_holder_experiment_id.is_not(None),
            Experiment.lock_acquired_at.is_not(None),
        )
    )
    if exclude_id is not None:
        stmt = stmt.where(Experiment.id != exclude_id)
    return db.scalar(stmt)


def acquire_experiment_lock(
    db: Session,
    experiment_id: uuid.UUID,
    actor: Agent,
    *,
    ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
) -> LockResult:
    """Acquire the per-project execution lock for ``experiment_id``.

    Raises :class:`ConflictError` when another live experiment in the same
    project holds the lock. A stale lock (TTL expired) is transparently
    reclaimed.
    """

    experiment = _get(db, experiment_id)
    _ensure_can_modify_lock(actor, experiment)

    other = _find_project_holder(db, experiment.project_id, exclude_id=experiment_id)
    if other is not None and other.lock_holder_experiment_id != experiment.id and not _is_expired(other):
        raise ConflictError(
            f"experiment {other.id} already holds the execution lock for project {experiment.project_id}"
        )

    experiment.lock_holder_experiment_id = experiment.id
    experiment.lock_acquired_at = _now()
    experiment.lock_ttl_seconds = int(ttl_seconds)
    try:
        commit_with_retry(db, op_label="acquire_experiment_lock")
    except IntegrityError:
        # Lost the race against another acquire that committed first; the
        # ``uq_experiment_lock_holder_active`` partial unique index
        # (PG-only; race experiment eca0f522 PR1) caught the conflict at
        # commit time. Surface as ConflictError so callers see the same
        # semantics as the in-process ``_find_project_holder`` guard.
        db.rollback()
        holder = _find_project_holder(db, experiment.project_id, exclude_id=experiment_id)
        holder_id = holder.id if holder is not None else None
        raise ConflictError(
            f"experiment {holder_id} already holds the execution lock for project {experiment.project_id}"
        ) from None
    db.refresh(experiment)
    logger.info(
        "acquire_lock experiment=%s project=%s ttl=%s",
        experiment.id,
        experiment.project_id,
        ttl_seconds,
    )
    return _to_result(experiment)


def release_experiment_lock(
    db: Session,
    experiment_id: uuid.UUID,
    actor: Agent,
) -> LockResult:
    """Release the lock if ``experiment_id`` is the current holder."""

    experiment = _get(db, experiment_id)
    _ensure_can_modify_lock(actor, experiment)

    if experiment.lock_holder_experiment_id == experiment.id:
        experiment.lock_holder_experiment_id = None
        experiment.lock_acquired_at = None
        experiment.lock_ttl_seconds = None
        commit_with_retry(db, op_label="release_experiment_lock")
        db.refresh(experiment)
        logger.info("release_lock experiment=%s", experiment.id)
    else:
        logger.info(
            "release_lock noop experiment=%s current_holder=%s",
            experiment.id,
            experiment.lock_holder_experiment_id,
        )
    return _to_result(experiment)


def force_release_experiment_lock(
    db: Session,
    experiment_id: uuid.UUID,
    actor: Agent,
    *,
    reason: str,
) -> LockResult:
    """Operator override: clear the lock regardless of holder.

    Clears **all** lock holders on the project (only one should exist, but
    defensive).
    """

    experiment = _get(db, experiment_id)
    if actor.role.value != "admin" and experiment.creator_agent_id != actor.id:
        raise ForbiddenError("Only the creator or admin can force-release a lock")

    # Clear every holder on the project.
    holders = (
        db.scalars(
            select(Experiment).where(
                Experiment.project_id == experiment.project_id,
                Experiment.lock_holder_experiment_id.is_not(None),
            )
        ).all()
    )
    previous_holders = [str(e.id) for e in holders if e.lock_holder_experiment_id is not None]
    for holder in holders:
        holder.lock_holder_experiment_id = None
        holder.lock_acquired_at = None
        holder.lock_ttl_seconds = None
    commit_with_retry(db, op_label="force_release_experiment_lock")
    logger.warning(
        "force_release_lock actor=%s reason=%s project=%s previous_holders=%s",
        actor.id,
        reason,
        experiment.project_id,
        previous_holders,
    )
    db.refresh(experiment)
    return _to_result(experiment)


def record_experiment_lock_skip(
    db: Session,
    experiment_id: uuid.UUID,
    actor: Agent,
    *,
    next_attempt_at: datetime,
) -> LockResult:
    """Bump ``lock_skip_count`` and record ``next_attempt_at`` for closed-loop backoff."""

    experiment = _get(db, experiment_id)
    _ensure_can_modify_lock(actor, experiment)
    experiment.lock_skip_count = int(experiment.lock_skip_count or 0) + 1
    experiment.next_attempt_at = next_attempt_at
    commit_with_retry(db, op_label="record_experiment_lock_skip")
    db.refresh(experiment)
    logger.info(
        "lock_skip experiment=%s skip_count=%s next_attempt_at=%s",
        experiment.id,
        experiment.lock_skip_count,
        next_attempt_at.isoformat(),
    )
    return _to_result(experiment)


def serialize_lock_state(experiment: Experiment) -> dict[str, Any]:
    """Return a JSON-safe snapshot for inclusion in API responses / logs."""

    return {
        "lock_holder_experiment_id": str(experiment.lock_holder_experiment_id) if experiment.lock_holder_experiment_id else None,
        "lock_acquired_at": experiment.lock_acquired_at.isoformat() if experiment.lock_acquired_at else None,
        "lock_ttl_seconds": experiment.lock_ttl_seconds,
        "next_attempt_at": experiment.next_attempt_at.isoformat() if experiment.next_attempt_at else None,
        "lock_skip_count": int(experiment.lock_skip_count or 0),
    }


__all__ = [
    "DEFAULT_LOCK_TTL_SECONDS",
    "LockResult",
    "acquire_experiment_lock",
    "force_release_experiment_lock",
    "record_experiment_lock_skip",
    "release_experiment_lock",
    "serialize_lock_state",
]
