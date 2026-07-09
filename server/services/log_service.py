import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.domain.models import (
    Agent,
    ExperimentLog,
    ExperimentPhase,
    PlanVersion,
)
from server.domain.schemas import ExperimentLogCreate
from server.services import audit_service
from server.services.errors import StateTransitionError
from server.services.evidence_service import (
    EvidenceValidationResult,
    validate_log_evidence,
)
from server.services.project_service import get_experiment
from server.services.similarity_service import (
    SimilarityValidationResult,
    validate_log_similarity,
)


def _validate_log_phase(phase: ExperimentPhase) -> None:
    if phase not in (
        ExperimentPhase.running,
        ExperimentPhase.result_review,
        ExperimentPhase.done,
    ):
        raise StateTransitionError(
            "Logs can only be added when experiment is running, pending result review, or done"
        )


def _load_current_plan_md(db: Session, experiment_id: uuid.UUID) -> str | None:
    """Return ``current_plan.content_md`` for the experiment, or None when
    the experiment has no committed plan version (current_plan_version == 0
    or the row is missing).
    """
    experiment = get_experiment(db, experiment_id)
    if experiment.current_plan_version <= 0:
        return None
    plan = db.scalar(
        select(PlanVersion).where(
            PlanVersion.experiment_id == experiment_id,
            PlanVersion.version == experiment.current_plan_version,
        )
    )
    return plan.content_md if plan is not None else None


def append_log(
    db: Session,
    experiment_id: uuid.UUID,
    author: Agent,
    payload: ExperimentLogCreate,
) -> tuple[ExperimentLog, EvidenceValidationResult, SimilarityValidationResult, bool]:
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
    plan_md = _load_current_plan_md(db, experiment_id)
    validation = validate_log_evidence(plan_md=plan_md, metadata=payload.metadata)
    # Compute similarity BEFORE flushing the new log so the comparison
    # sees the prior log, not the log we're about to save (which would
    # trivially score 1.0 against itself).
    similarity = validate_log_similarity(
        db,
        experiment_id=experiment_id,
        content_md=payload.content_md,
    )
    db.add(log)
    db.flush()
    force_skip_applied = False
    if similarity.warnings and payload.force_skip_similarity:
        warning = similarity.warnings[0]
        audit_service.log_force_skip_no_commit(
            db,
            log_id=log.id,
            ref_log_id=warning.ref_log_id,
            experiment_id=experiment_id,
            project_id=experiment.project_id,
            actor_id=author.id,
            similarity_score=warning.score,
            threshold=warning.threshold,
            embedding_model=warning.model,
        )
        force_skip_applied = True
    return log, validation, similarity, force_skip_applied


def create_log(
    db: Session,
    experiment_id: uuid.UUID,
    author: Agent,
    payload: ExperimentLogCreate,
) -> tuple[
    ExperimentLog,
    EvidenceValidationResult,
    SimilarityValidationResult,
    bool,
]:
    log, validation, similarity, force_skip_applied = append_log(
        db, experiment_id, author, payload
    )
    db.commit()
    db.refresh(log)
    return log, validation, similarity, force_skip_applied


def list_logs(
    db: Session,
    experiment_id: uuid.UUID,
    *,
    limit: int = 50,
) -> list[ExperimentLog]:
    get_experiment(db, experiment_id)
    stmt = (
        select(ExperimentLog)
        .where(ExperimentLog.experiment_id == experiment_id)
        .order_by(ExperimentLog.log_index.asc())
        .limit(max(1, min(limit, 200)))
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
