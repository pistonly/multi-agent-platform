import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.domain.models import Agent, ExperimentLog, ExperimentPhase
from server.domain.schemas import ExperimentLogCreate
from server.services.errors import StateTransitionError
from server.services.project_service import get_experiment


def _validate_log_phase(phase: ExperimentPhase) -> None:
    if phase not in (ExperimentPhase.running, ExperimentPhase.done):
        raise StateTransitionError("Logs can only be added when experiment is running or done")


def append_log(
    db: Session,
    experiment_id: uuid.UUID,
    author: Agent,
    payload: ExperimentLogCreate,
) -> ExperimentLog:
    experiment = get_experiment(db, experiment_id)
    _validate_log_phase(experiment.phase)
    next_index = db.scalar(
        select(func.coalesce(func.max(ExperimentLog.log_index), 0) + 1).where(
            ExperimentLog.experiment_id == experiment_id
        )
    )
    log = ExperimentLog(
        experiment_id=experiment_id,
        author_agent_id=author.id,
        summary=payload.summary,
        content_md=payload.content_md,
        metadata_json=payload.metadata,
        log_index=next_index or 1,
    )
    db.add(log)
    return log


def create_log(
    db: Session,
    experiment_id: uuid.UUID,
    author: Agent,
    payload: ExperimentLogCreate,
) -> ExperimentLog:
    log = append_log(db, experiment_id, author, payload)
    db.commit()
    db.refresh(log)
    return log


def list_logs(db: Session, experiment_id: uuid.UUID) -> list[ExperimentLog]:
    get_experiment(db, experiment_id)
    stmt = (
        select(ExperimentLog)
        .where(ExperimentLog.experiment_id == experiment_id)
        .order_by(ExperimentLog.log_index.asc())
    )
    return list(db.scalars(stmt))


def get_latest_log(db: Session, experiment_id: uuid.UUID) -> ExperimentLog | None:
    stmt = (
        select(ExperimentLog)
        .where(ExperimentLog.experiment_id == experiment_id)
        .order_by(ExperimentLog.log_index.desc())
        .limit(1)
    )
    return db.scalar(stmt)
