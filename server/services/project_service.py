import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.domain.models import Agent, Experiment, ExperimentPhase, PlanVersion, Project
from server.domain.schemas import (
    ExperimentCreate,
    ExperimentDetailRead,
    ExperimentSummaryRead,
    ExperimentUpdate,
    PlanVersionRead,
    ProjectCreate,
    ProjectRead,
    ProjectStatusRead,
    ProjectUpdate,
)
from server.services.errors import ConflictError, NotFoundError
from server.services import project_status_service as status_doc_service


def create_project(db: Session, payload: ProjectCreate, *, author_agent_id: uuid.UUID) -> Project:
    existing = db.scalar(select(Project).where(Project.project_key == payload.project_key))
    if existing is not None:
        raise ConflictError("project_key already exists")
    project = Project(**payload.model_dump())
    db.add(project)
    db.flush()
    status_doc_service.create_initial_status(db, project=project, author_agent_id=author_agent_id)
    db.commit()
    db.refresh(project)
    return project


def list_projects(
    db: Session,
    *,
    include_archived: bool = False,
    project_id: uuid.UUID | None = None,
) -> list[Project]:
    stmt = select(Project).order_by(Project.created_at.desc())
    if project_id is not None:
        stmt = stmt.where(Project.id == project_id)
    if not include_archived:
        stmt = stmt.where(Project.archived_at.is_(None))
    return list(db.scalars(stmt))


def get_project(db: Session, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return project


def get_project_by_key(db: Session, project_key: str) -> Project:
    project = db.scalar(select(Project).where(Project.project_key == project_key))
    if project is None:
        raise NotFoundError("Project not found")
    return project


def update_project(db: Session, project_id: uuid.UUID, payload: ProjectUpdate) -> Project:
    project = get_project(db, project_id)
    data = payload.model_dump(exclude_unset=True)
    archived = data.pop("archived", None)
    for key, value in data.items():
        setattr(project, key, value)
    if archived is not None:
        project.archived_at = datetime.now(UTC) if archived else None
    db.commit()
    db.refresh(project)
    return project


def get_project_status(db: Session, project_id: uuid.UUID) -> ProjectStatusRead:
    project = get_project(db, project_id)
    counts_stmt = (
        select(Experiment.phase, func.count())
        .where(Experiment.project_id == project_id, Experiment.deleted_at.is_(None))
        .group_by(Experiment.phase)
    )
    counts = {phase.value: count for phase, count in db.execute(counts_stmt)}
    for phase in ExperimentPhase:
        counts.setdefault(phase.value, 0)

    active_phases = (
        ExperimentPhase.draft,
        ExperimentPhase.review,
        ExperimentPhase.approved,
        ExperimentPhase.running,
    )
    active_stmt = (
        select(Experiment)
        .where(
            Experiment.project_id == project_id,
            Experiment.deleted_at.is_(None),
            Experiment.phase.in_(active_phases),
        )
        .order_by(Experiment.updated_at.desc())
    )
    active = [ExperimentSummaryRead.model_validate(e) for e in db.scalars(active_stmt)]

    recent_stmt = (
        select(Experiment)
        .where(Experiment.project_id == project_id, Experiment.deleted_at.is_(None))
        .order_by(Experiment.updated_at.desc())
        .limit(5)
    )
    recent = [ExperimentSummaryRead.model_validate(e) for e in db.scalars(recent_stmt)]

    status_version, status_md, status_updated_at = status_doc_service.get_current_status_md(db, project_id)

    return ProjectStatusRead(
        project=ProjectRead.model_validate(project),
        experiment_counts_by_phase=counts,
        active_experiments=active,
        recent_experiments=recent,
        status_version=status_version,
        status_md=status_md,
        status_updated_at=status_updated_at,
    )


def create_experiment(
    db: Session,
    project_id: uuid.UUID,
    creator_agent_id: uuid.UUID,
    payload: ExperimentCreate,
) -> Experiment:
    get_project(db, project_id)
    phase = ExperimentPhase.review if payload.submit_for_review else ExperimentPhase.draft
    experiment = Experiment(
        project_id=project_id,
        creator_agent_id=creator_agent_id,
        title=payload.title,
        description=payload.description,
        phase=phase,
        current_plan_version=1,
    )
    db.add(experiment)
    db.flush()

    plan = PlanVersion(
        experiment_id=experiment.id,
        version=1,
        content_md=payload.plan.content_md,
        author_agent_id=creator_agent_id,
        change_note=payload.plan.change_note or "初始版本",
    )
    db.add(plan)
    db.commit()
    db.refresh(experiment)
    return experiment


def list_experiments(
    db: Session,
    project_id: uuid.UUID,
    *,
    phase: ExperimentPhase | None = None,
) -> list[Experiment]:
    get_project(db, project_id)
    stmt = (
        select(Experiment)
        .where(Experiment.project_id == project_id, Experiment.deleted_at.is_(None))
        .order_by(Experiment.updated_at.desc())
    )
    if phase is not None:
        stmt = stmt.where(Experiment.phase == phase)
    return list(db.scalars(stmt))


def get_experiment(db: Session, experiment_id: uuid.UUID) -> Experiment:
    experiment = db.get(Experiment, experiment_id)
    if experiment is None or experiment.deleted_at is not None:
        raise NotFoundError("Experiment not found")
    return experiment


def get_experiment_detail(db: Session, experiment_id: uuid.UUID) -> ExperimentDetailRead:
    from server.services.log_service import get_latest_log
    from server.services.review_service import count_open_unreasonable_for_experiment

    experiment = get_experiment(db, experiment_id)
    current_plan = None
    if experiment.current_plan_version > 0:
        plan_stmt = select(PlanVersion).where(
            PlanVersion.experiment_id == experiment.id,
            PlanVersion.version == experiment.current_plan_version,
        )
        plan = db.scalar(plan_stmt)
        if plan:
            current_plan = PlanVersionRead.model_validate(plan)

    latest = get_latest_log(db, experiment.id)

    return ExperimentDetailRead(
        id=experiment.id,
        project_id=experiment.project_id,
        creator_agent_id=experiment.creator_agent_id,
        title=experiment.title,
        description=experiment.description,
        phase=experiment.phase,
        current_plan_version=experiment.current_plan_version,
        created_at=experiment.created_at,
        updated_at=experiment.updated_at,
        current_plan=current_plan,
        plan_version_count=len(experiment.plan_versions),
        open_unreasonable_count=count_open_unreasonable_for_experiment(db, experiment.id),
        review_count=len(experiment.reviews),
        log_count=len(experiment.logs),
        latest_log_summary=latest.summary if latest else None,
    )


def update_experiment(
    db: Session,
    experiment_id: uuid.UUID,
    payload: ExperimentUpdate,
) -> Experiment:
    experiment = get_experiment(db, experiment_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(experiment, key, value)
    db.commit()
    db.refresh(experiment)
    return experiment


def soft_delete_experiment(db: Session, experiment_id: uuid.UUID) -> None:
    experiment = get_experiment(db, experiment_id)
    experiment.deleted_at = datetime.now(UTC)
    db.commit()
