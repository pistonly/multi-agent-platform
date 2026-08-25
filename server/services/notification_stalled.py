"""Stalled experiment-lock scan notifications（T17 从 notification_service 拆出）。

职责边界：``notify_stalled_experiment_locks`` 及其私有辅助（``_latest_experiment_log_at_batch`` / ``_project_agent_ids``；时区归一化已上移 ``time_utils.as_utc``）。这一簇只被
``POST /experiments/scan-stalled-locks`` 周期触发，与 inbox（list/mark read）
和 fanout（upsert / emit_kind）两个簇无共享状态——独立成模块后
``notification_service`` 保留约 850 行的 fanout + inbox 面，并通过
re-export 维持 ``from server.services.notification_service import
notify_stalled_experiment_locks`` 的既有导入路径。

依赖方向：本模块顶层只 import models / enums；对 fanout 的
``enqueue_for_agents`` 采用函数内 lazy import——反方向（notification_service
re-export 本模块）为顶层 import，避免导入环。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import cast

from map_types.enums import ExperimentPhase
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.domain.models import Agent, Experiment, ExperimentLog
from server.services.time_utils import as_utc


def _latest_experiment_log_at_batch(
    db: Session, experiment_ids: list[uuid.UUID]
) -> dict[uuid.UUID, datetime | None]:
    """T15：一条分组查询取多个实验的最新日志时间（替代逐实验 SELECT）。"""
    if not experiment_ids:
        return {}
    stmt = (
        select(ExperimentLog.experiment_id, func.max(ExperimentLog.created_at))
        .where(ExperimentLog.experiment_id.in_(experiment_ids))
        .group_by(ExperimentLog.experiment_id)
    )
    latest: dict[uuid.UUID, datetime | None] = {}
    for row in db.execute(stmt):
        latest[cast(uuid.UUID, row[0])] = cast(datetime | None, row[1])
    return latest


def _project_agent_ids(db: Session, project_id: uuid.UUID, *, exclude: set[uuid.UUID]) -> list[uuid.UUID]:
    stmt = select(Agent.id).where(Agent.project_id == project_id)
    if exclude:
        stmt = stmt.where(Agent.id.notin_(exclude))
    rows = db.scalars(stmt).all()
    return list(rows)


def notify_stalled_experiment_locks(
    db: Session,
    *,
    project_id: uuid.UUID | None = None,
    now: datetime | None = None,
    progress_threshold: float = 0.5,
    wake_threshold: float = 0.8,
    commit: bool = True,
) -> list[uuid.UUID]:
    """Notify when a running experiment holds the execution lock without progress.

    D experiment slice:
    - holder/host gets a wakeable notification once the lock has consumed at
      least ``wake_threshold`` of its TTL without a new execution log.
    - project members get a digest notification once the lock has consumed at
      least ``progress_threshold`` of its TTL without a new execution log.

    Notification grouping keeps repeated scans from creating many rows; wakeable
    upserts still bump ``wake_version`` so waker fingerprints can advance.

    T15（2026-08）：消循环内 N+1。原先每个实验各发一条 max(created_at)
    SELECT 与一条 project agent SELECT；现在第一遍先在 Python 里按
    ``progress_threshold`` 过滤出候选，再一条分组 IN 查询取齐全部候选的
    最新日志时间，project agent 列表每 project 只查一次（holder 排除改
    在 Python 侧做，缓存的是无排除的原始列表）。
    """
    # lazy import：避免与 notification_service 的 re-export 构成导入环。
    from server.services.notification_service import enqueue_for_agents

    reference = as_utc(now or datetime.now(timezone.utc))
    emitted: list[uuid.UUID] = []
    filters = [
        Experiment.phase == ExperimentPhase.running,
        Experiment.lock_holder_experiment_id.is_not(None),
        Experiment.lock_acquired_at.is_not(None),
    ]
    if project_id is not None:
        filters.append(Experiment.project_id == project_id)
    experiments = db.scalars(select(Experiment).where(*filters)).all()

    # Pass 1：纯 Python 过滤（锁字段都在 experiment 行上），定出候选集。
    candidates: list[tuple[Experiment, datetime, int, float]] = []
    for experiment in experiments:
        acquired_at = as_utc(experiment.lock_acquired_at)
        if acquired_at is None:
            continue
        ttl_seconds = int(experiment.lock_ttl_seconds or 0)
        if ttl_seconds <= 0:
            continue
        elapsed = max(0.0, (reference - acquired_at).total_seconds())
        ratio = elapsed / ttl_seconds
        if ratio < progress_threshold:
            continue
        candidates.append((experiment, acquired_at, ttl_seconds, ratio))

    # Pass 1.5：一条分组查询取齐候选的最新日志时间（T15 消 N+1）。
    latest_log_at = _latest_experiment_log_at_batch(
        db, [experiment.id for experiment, _, _, _ in candidates]
    )
    # project agent 列表每 project 查一次；holder 排除在 Python 侧做。
    project_agents: dict[uuid.UUID, list[uuid.UUID]] = {}

    for experiment, acquired_at, ttl_seconds, ratio in candidates:
        last_log_at = as_utc(latest_log_at.get(experiment.id))
        if last_log_at is not None and last_log_at > acquired_at:
            continue
        payload: dict[str, object] = {
            "experiment_id": str(experiment.id),
            "lock_holder_experiment_id": str(experiment.lock_holder_experiment_id),
            "lock_acquired_at": acquired_at.isoformat(),
            "lock_ttl_seconds": ttl_seconds,
            "elapsed_seconds": int(elapsed),
            "threshold_ratio": ratio,
            "last_log_at": last_log_at.isoformat() if last_log_at else None,
        }
        summary = f"实验锁长时间无进展：{experiment.title}"
        holder_ids = [experiment.creator_agent_id]
        if ratio >= wake_threshold:
            emitted.extend(
                enqueue_for_agents(
                    db,
                    recipient_agent_ids=holder_ids,
                    project_id=experiment.project_id,
                    actor_id=experiment.creator_agent_id,
                    event="experiment.lock.no_progress",
                    summary=summary,
                    target_type="experiment",
                    target_id=experiment.id,
                    payload=payload,
                    wakeable=True,
                    exclude_actor=False,
                    commit=False,
                )
            )
        members = project_agents.get(experiment.project_id)
        if members is None:
            members = _project_agent_ids(db, experiment.project_id, exclude=set())
            project_agents[experiment.project_id] = members
        holder_set = set(holder_ids)
        digest_recipients = [member for member in members if member not in holder_set]
        emitted.extend(
            enqueue_for_agents(
                db,
                recipient_agent_ids=digest_recipients,
                project_id=experiment.project_id,
                actor_id=experiment.creator_agent_id,
                event="experiment.lock.no_progress",
                summary=summary,
                target_type="experiment",
                target_id=experiment.id,
                payload=payload,
                wakeable=False,
                exclude_actor=False,
                commit=False,
            )
        )
    if commit:
        db.commit()
    else:
        db.flush()
    return emitted
