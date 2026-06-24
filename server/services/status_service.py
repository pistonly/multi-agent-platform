import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.domain.models import Experiment, ExperimentPhase
from server.domain.schemas import ExperimentSummaryRead, GlobalStatusRead
from server.services.errors import NotFoundError
from server.services.project_service import get_project, get_project_status, list_projects


def get_global_status(db: Session, *, project_id: uuid.UUID | None = None) -> GlobalStatusRead:
    if project_id is not None:
        get_project(db, project_id)
        project_status = get_project_status(db, project_id)
        counts_stmt = (
            select(Experiment.phase, func.count())
            .where(Experiment.project_id == project_id, Experiment.deleted_at.is_(None))
            .group_by(Experiment.phase)
        )
        counts = {phase.value: count for phase, count in db.execute(counts_stmt)}
        for phase in ExperimentPhase:
            counts.setdefault(phase.value, 0)
        recent_stmt = (
            select(Experiment)
            .where(Experiment.project_id == project_id, Experiment.deleted_at.is_(None))
            .order_by(Experiment.updated_at.desc())
            .limit(10)
        )
        recent = [ExperimentSummaryRead.model_validate(e) for e in db.scalars(recent_stmt)]
        return GlobalStatusRead(
            total_experiments_by_phase=counts,
            projects=[project_status],
            recent_experiments=recent,
        )

    projects = list_projects(db)
    project_statuses = [get_project_status(db, p.id) for p in projects]

    counts_stmt = (
        select(Experiment.phase, func.count())
        .where(Experiment.deleted_at.is_(None))
        .group_by(Experiment.phase)
    )
    counts = {phase.value: count for phase, count in db.execute(counts_stmt)}
    for phase in ExperimentPhase:
        counts.setdefault(phase.value, 0)

    recent_stmt = (
        select(Experiment)
        .where(Experiment.deleted_at.is_(None))
        .order_by(Experiment.updated_at.desc())
        .limit(10)
    )
    recent = [ExperimentSummaryRead.model_validate(e) for e in db.scalars(recent_stmt)]

    return GlobalStatusRead(
        total_experiments_by_phase=counts,
        projects=project_statuses,
        recent_experiments=recent,
    )
