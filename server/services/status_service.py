import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.domain.models import Agent, Experiment, ExperimentPhase
from server.domain.schemas import (
    ExperimentSummaryRead,
    GlobalStatusRead,
    WakerHeartbeatRead,
)
from server.services.project_service import build_projects_status, get_project, list_projects


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_waker_heartbeats(
    db: Session,
    *,
    project_id: uuid.UUID | None = None,
    threshold_minutes: int | None = None,
    now: datetime | None = None,
) -> list[WakerHeartbeatRead]:
    """Per-agent waker heartbeat rows with server-computed ``stale`` flag.

    stale = ever heartbeated (last_waker_poll_at not null) AND older than the
    threshold. null last_waker_poll_at → ``never``, NOT stale → no WARN, so the
    "only one waker" deployment (other personas never poll) stays quiet (D1 null
    semantics). Threshold falls back to ``Settings.waker_stale_threshold_minutes``
    (env ``MAP_WAKER_STALE_THRESHOLD_MINUTES``, default 15).
    """
    from server.config import get_settings

    if threshold_minutes is None:
        threshold_minutes = get_settings().waker_stale_threshold_minutes
    cutoff = _as_utc(now or datetime.now(timezone.utc)) - timedelta(
        minutes=threshold_minutes
    )
    stmt = select(Agent)
    if project_id is not None:
        stmt = stmt.where(Agent.project_id == project_id)
    rows: list[WakerHeartbeatRead] = []
    for agent in db.scalars(stmt):
        last = agent.last_waker_poll_at
        stale = last is not None and _as_utc(last) < cutoff
        rows.append(
            WakerHeartbeatRead(
                agent_id=agent.id,
                agent_name=agent.name,
                persona=agent.persona,
                last_waker_poll_at=last,
                stale=stale,
            )
        )
    return rows


def get_global_status(db: Session, *, project_id: uuid.UUID | None = None) -> GlobalStatusRead:
    if project_id is not None:
        project = get_project(db, project_id)
        project_status = build_projects_status(db, [project])[0]
        counts_stmt = (
            select(Experiment.phase, func.count())
            .where(
                Experiment.project_id == project_id,
                Experiment.deleted_at.is_(None),
                Experiment.archived_at.is_(None),
            )
            .group_by(Experiment.phase)
        )
        counts = {phase.value: count for phase, count in db.execute(counts_stmt)}
        for phase in ExperimentPhase:
            counts.setdefault(phase.value, 0)
        recent_stmt = (
            select(Experiment)
            .where(
                Experiment.project_id == project_id,
                Experiment.deleted_at.is_(None),
                Experiment.archived_at.is_(None),
            )
            .order_by(Experiment.updated_at.desc())
            .limit(10)
        )
        recent = [ExperimentSummaryRead.model_validate(e) for e in db.scalars(recent_stmt)]
        return GlobalStatusRead(
            total_experiments_by_phase=counts,
            projects=[project_status],
            recent_experiments=recent,
            waker_heartbeats=build_waker_heartbeats(db, project_id=project_id),
        )

    projects = list_projects(db)
    project_statuses = build_projects_status(db, projects)

    counts_stmt = (
        select(Experiment.phase, func.count())
        .where(Experiment.deleted_at.is_(None), Experiment.archived_at.is_(None))
        .group_by(Experiment.phase)
    )
    counts = {phase.value: count for phase, count in db.execute(counts_stmt)}
    for phase in ExperimentPhase:
        counts.setdefault(phase.value, 0)

    recent_stmt = (
        select(Experiment)
        .where(Experiment.deleted_at.is_(None), Experiment.archived_at.is_(None))
        .order_by(Experiment.updated_at.desc())
        .limit(10)
    )
    recent = [ExperimentSummaryRead.model_validate(e) for e in db.scalars(recent_stmt)]

    return GlobalStatusRead(
        total_experiments_by_phase=counts,
        projects=project_statuses,
        recent_experiments=recent,
        waker_heartbeats=build_waker_heartbeats(db),
    )
