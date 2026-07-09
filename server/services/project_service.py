import uuid
from datetime import UTC, datetime

from map_types.enums import TopicActionItemStatus, TopicDiscussionRound, TopicStatus
from map_types.schemas import TopicSummaryRead
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.domain.models import (
    Agent,
    AgentRole,
    Experiment,
    ExperimentPhase,
    PlanVersion,
    Project,
    ProjectStatusVersion,
    Topic,
)
from server.domain.schemas import (
    ExperimentBundleRead,
    ExperimentCreate,
    ExperimentDetailRead,
    ExperimentLogRead,
    ExperimentSummaryRead,
    ExperimentUpdate,
    PlanVersionRead,
    ProjectCreate,
    ProjectRead,
    ProjectStatusRead,
    ProjectUpdate,
    TopicActionItemRead,
    TopicDecisionRead,
)
from server.domain.state_machine import TERMINAL_PHASES
from server.services import project_status_service as status_doc_service
from server.services import topic_service

# ``get_project`` 下沉到 ``_lookups`` 以打破 project_service ↔ topic_service 循环 import；
# 这里 re-export 保持 ``from server.services.project_service import get_project`` 兼容。
from server.services._lookups import get_project
from server.services.acceptance_service import parse_acceptance_status
from server.services.errors import ConflictError, ForbiddenError, NotFoundError

_ACTIVE_TOPIC_EXPERIMENT_PHASES = (
    ExperimentPhase.draft,
    ExperimentPhase.review,
    ExperimentPhase.approved,
    ExperimentPhase.running,
    ExperimentPhase.result_review,
)


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
    return build_projects_status(db, [project])[0]


def list_project_decisions(
    db: Session,
    project_id: uuid.UUID,
    *,
    limit: int = 20,
) -> list[TopicDecisionRead]:
    get_project(db, project_id)
    return topic_service.list_project_decisions(db, project_id, limit=limit)


def list_project_action_items(
    db: Session,
    project_id: uuid.UUID,
    *,
    owner_agent_id: uuid.UUID | None = None,
    status: TopicActionItemStatus | None = None,
    limit: int = 100,
) -> list[TopicActionItemRead]:
    get_project(db, project_id)
    return topic_service.list_action_items(
        db,
        project_id,
        owner_agent_id=owner_agent_id,
        status=status,
        limit=limit,
    )


def build_projects_status(db: Session, projects: list[Project]) -> list[ProjectStatusRead]:
    if not projects:
        return []

    project_ids = [project.id for project in projects]
    counts_map: dict[uuid.UUID, dict[str, int]] = {pid: {} for pid in project_ids}
    counts_stmt = (
        select(Experiment.project_id, Experiment.phase, func.count())
        .where(
            Experiment.project_id.in_(project_ids),
            Experiment.deleted_at.is_(None),
            Experiment.archived_at.is_(None),
        )
        .group_by(Experiment.project_id, Experiment.phase)
    )
    for project_id, phase, count in db.execute(counts_stmt):
        counts_map[project_id][phase.value] = count
    for project_id in project_ids:
        for phase in ExperimentPhase:
            counts_map[project_id].setdefault(phase.value, 0)

    active_phases = (
        ExperimentPhase.draft,
        ExperimentPhase.review,
        ExperimentPhase.approved,
        ExperimentPhase.running,
        ExperimentPhase.result_review,
    )
    active_map: dict[uuid.UUID, list[ExperimentSummaryRead]] = {pid: [] for pid in project_ids}
    active_stmt = (
        select(Experiment)
        .where(
            Experiment.project_id.in_(project_ids),
            Experiment.deleted_at.is_(None),
            Experiment.archived_at.is_(None),
            Experiment.phase.in_(active_phases),
        )
        .order_by(Experiment.project_id, Experiment.updated_at.desc())
    )
    for experiment in db.scalars(active_stmt):
        active_map[experiment.project_id].append(ExperimentSummaryRead.model_validate(experiment))

    recent_map: dict[uuid.UUID, list[ExperimentSummaryRead]] = {pid: [] for pid in project_ids}
    recent_stmt = (
        select(Experiment)
        .where(
            Experiment.project_id.in_(project_ids),
            Experiment.deleted_at.is_(None),
            Experiment.archived_at.is_(None),
        )
        .order_by(Experiment.project_id, Experiment.updated_at.desc())
    )
    for experiment in db.scalars(recent_stmt):
        recent = recent_map[experiment.project_id]
        if len(recent) < 5:
            recent.append(ExperimentSummaryRead.model_validate(experiment))

    open_topics_by_project: dict[uuid.UUID, list[Topic]] = {pid: [] for pid in project_ids}
    open_topics_stmt = (
        select(Topic)
        .where(
            Topic.project_id.in_(project_ids),
            Topic.deleted_at.is_(None),
            Topic.archived_at.is_(None),
            Topic.status == TopicStatus.open,
        )
        .order_by(Topic.pinned.desc(), Topic.updated_at.desc())
    )
    for topic in db.scalars(open_topics_stmt):
        open_topics_by_project[topic.project_id].append(topic)

    open_topics_map: dict[uuid.UUID, list[TopicSummaryRead]] = {
        project_id: topic_service.topic_summaries_for_topics(db, topics)
        for project_id, topics in open_topics_by_project.items()
    }

    status_rows = list(
        db.scalars(select(ProjectStatusVersion).where(ProjectStatusVersion.project_id.in_(project_ids)))
    )
    status_by_key = {(row.project_id, row.version): row for row in status_rows}

    results: list[ProjectStatusRead] = []
    for project in projects:
        status_version = 0
        status_md: str | None = None
        status_updated_at = None
        if project.current_status_version > 0:
            row = status_by_key.get((project.id, project.current_status_version))
            if row is not None:
                status_version = row.version
                status_md = row.content_md
                status_updated_at = row.created_at
            else:
                status_version = project.current_status_version

        results.append(
            ProjectStatusRead(
                project=ProjectRead.model_validate(project),
                experiment_counts_by_phase=counts_map[project.id],
                active_experiments=active_map[project.id],
                recent_experiments=recent_map[project.id],
                open_topics=open_topics_map[project.id],
                status_version=status_version,
                status_md=status_md,
                status_updated_at=status_updated_at,
            )
        )
    return results


def create_experiment_warnings(
    db: Session,
    project_id: uuid.UUID,
    topic_id: uuid.UUID | None,
) -> list[str]:
    if topic_id is not None:
        topic = db.get(Topic, topic_id)
        if topic is None or topic.deleted_at is not None or topic.project_id != project_id:
            return []
        if topic.discussion_round != TopicDiscussionRound.ready:
            return ["topic_not_ready_for_experiment"]
        return []
    open_count = (
        db.scalar(
            select(func.count())
            .select_from(Topic)
            .where(
                Topic.project_id == project_id,
                Topic.status == TopicStatus.open,
                Topic.deleted_at.is_(None),
            )
        )
        or 0
    )
    if open_count > 0:
        return ["no_topic_id"]
    return []


def create_experiment(
    db: Session,
    project_id: uuid.UUID,
    creator_agent_id: uuid.UUID,
    payload: ExperimentCreate,
) -> Experiment:
    get_project(db, project_id)
    # a764abf6 I1.(a): enforce plan frontmatter lint at create time
    # so missing required fields fail with STATE_MACHINE_PLAN_MARKER_MISSING
    # instead of writing a plan that will be rejected at revision.
    from server.services.plan_marker_service import assert_plan_frontmatter_ok

    assert_plan_frontmatter_ok(payload.plan.content_md)
    if payload.topic_id is not None:
        topic = db.get(Topic, payload.topic_id)
        if topic is None or topic.deleted_at is not None or topic.project_id != project_id:
            raise NotFoundError("Topic not found")
        if topic.status != TopicStatus.open:
            raise ConflictError("Cannot create experiment on a closed topic")
        creator = db.get(Agent, creator_agent_id)
        if topic.creator_agent_id != creator_agent_id and (creator is None or creator.role != AgentRole.admin):
            raise ForbiddenError("Only the topic host can create an experiment from this topic")
        active = db.scalar(
            select(Experiment).where(
                Experiment.topic_id == payload.topic_id,
                Experiment.deleted_at.is_(None),
                Experiment.archived_at.is_(None),
                Experiment.phase.in_(_ACTIVE_TOPIC_EXPERIMENT_PHASES),
            )
        )
        if active is not None:
            raise ConflictError(
                f"Topic already has an active experiment ({active.id}); complete or cancel it first"
            )
    phase = ExperimentPhase.review if payload.submit_for_review else ExperimentPhase.draft
    # I1(b): mirror the phase_owner column to the resolver's answer at
    # creation time so the ``informational_only`` auto-classification
    # works for the create-with-submit path too (not just for the
    # post-create submit_for_review path, which goes through
    # ``phase_service.submit_for_review``).
    from server.services.phase_owner_resolver import owner_for

    experiment = Experiment(
        project_id=project_id,
        creator_agent_id=creator_agent_id,
        title=payload.title,
        description=payload.description,
        phase=phase,
        current_plan_version=1,
        topic_id=payload.topic_id,
        phase_owner=owner_for(phase).value,
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
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Topic already has an active experiment; complete or cancel it first"
        ) from exc
    db.refresh(experiment)
    return experiment


def list_experiments(
    db: Session,
    project_id: uuid.UUID,
    *,
    phase: ExperimentPhase | None = None,
    creator_agent_id: uuid.UUID | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 100,
    include_archived: bool = False,
) -> tuple[list[Experiment], int]:
    get_project(db, project_id)
    stmt = select(Experiment).where(
        Experiment.project_id == project_id, Experiment.deleted_at.is_(None)
    )
    if not include_archived:
        stmt = stmt.where(Experiment.archived_at.is_(None))
    if phase is not None:
        stmt = stmt.where(Experiment.phase == phase)
    if creator_agent_id is not None:
        stmt = stmt.where(Experiment.creator_agent_id == creator_agent_id)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(Experiment.title.ilike(pattern) | Experiment.description.ilike(pattern))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    stmt = (
        stmt.order_by(Experiment.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(db.scalars(stmt)), total


def get_experiment(db: Session, experiment_id: uuid.UUID) -> Experiment:
    experiment = db.get(Experiment, experiment_id)
    if experiment is None or experiment.deleted_at is not None:
        raise NotFoundError("Experiment not found")
    return experiment


def get_experiment_detail(
    db: Session, experiment_id: uuid.UUID, actor: Agent | None = None
) -> ExperimentDetailRead:
    from server.domain.models import ExperimentLog, Review
    from server.services.experiment_capabilities_service import (
        apply_capabilities_to_detail,
        compute_experiment_capabilities,
        compute_legacy_self_review,
    )
    from server.services.log_service import get_latest_log
    from server.services.phase_owner_resolver import (
        is_informational_only,
        owner_for,
    )
    from server.services.review_service import count_open_unreasonable_for_experiment

    experiment = get_experiment(db, experiment_id)
    current_plan = None
    acceptance_status = []
    latest = get_latest_log(db, experiment.id)
    if experiment.current_plan_version > 0:
        plan_stmt = select(PlanVersion).where(
            PlanVersion.experiment_id == experiment.id,
            PlanVersion.version == experiment.current_plan_version,
        )
        plan = db.scalar(plan_stmt)
        if plan:
            current_plan = PlanVersionRead.model_validate(plan)
            acceptance_status = parse_acceptance_status(
                plan.content_md,
                completion_metadata=latest.metadata_json if latest else None,
            )

    plan_version_count = db.scalar(
        select(func.count()).select_from(PlanVersion).where(PlanVersion.experiment_id == experiment.id)
    ) or 0
    review_count = db.scalar(
        select(func.count()).select_from(Review).where(Review.experiment_id == experiment.id)
    ) or 0
    log_count = db.scalar(
        select(func.count()).select_from(ExperimentLog).where(ExperimentLog.experiment_id == experiment.id)
    ) or 0

    detail = ExperimentDetailRead(
        id=experiment.id,
        project_id=experiment.project_id,
        creator_agent_id=experiment.creator_agent_id,
        title=experiment.title,
        description=experiment.description,
        phase=experiment.phase,
        current_plan_version=experiment.current_plan_version,
        topic_id=experiment.topic_id,
        created_at=experiment.created_at,
        updated_at=experiment.updated_at,
        archived_at=experiment.archived_at,
        current_plan=current_plan,
        plan_version_count=plan_version_count,
        open_unreasonable_count=count_open_unreasonable_for_experiment(db, experiment.id),
        review_count=review_count,
        acceptance_status=acceptance_status,
        log_count=log_count,
        latest_log_summary=latest.summary if latest else None,
        lock_holder_experiment_id=experiment.lock_holder_experiment_id,
        lock_acquired_at=experiment.lock_acquired_at,
        lock_ttl_seconds=experiment.lock_ttl_seconds,
        next_attempt_at=experiment.next_attempt_at,
        lock_skip_count=int(experiment.lock_skip_count or 0),
        # I1(b): phase_owner read straight from the ORM column — the
        # column is kept in sync by ``phase_service._sync_phase_owner``
        # on every transition and by ``create_experiment`` at creation
        # time, so this is the single source of truth.
        phase_owner=owner_for(experiment.phase),
    )
    if actor is not None:
        actions, blocked_on = compute_experiment_capabilities(db, experiment, actor)
        legacy = compute_legacy_self_review(db, experiment)
        informational_only = is_informational_only(
            experiment.phase, actions=actions, blocked_on=blocked_on
        )
        return apply_capabilities_to_detail(
            detail,
            actions,
            blocked_on,
            legacy_self_review=legacy,
            phase_owner=owner_for(experiment.phase),
            informational_only=informational_only,
        )
    return detail


def get_experiment_bundle(
    db: Session, experiment_id: uuid.UUID, actor: Agent | None = None
) -> ExperimentBundleRead:
    from server.services import comment_service, log_service, plan_service, review_service

    experiment = get_experiment_detail(db, experiment_id, actor)
    plans = [PlanVersionRead.model_validate(p) for p in plan_service.list_plans(db, experiment_id)]
    reviews = [review_service.review_to_read(db, r) for r in review_service.list_reviews(db, experiment_id)]
    comments = comment_service.build_comment_tree(db, comment_service.list_comments(db, experiment_id))
    logs = [ExperimentLogRead.model_validate(entry) for entry in log_service.list_logs(db, experiment_id)]
    return ExperimentBundleRead(
        experiment=experiment,
        plans=plans,
        reviews=reviews,
        comments=comments,
        logs=logs,
    )


def update_experiment(
    db: Session,
    experiment_id: uuid.UUID,
    payload: ExperimentUpdate,
) -> Experiment:
    experiment = get_experiment(db, experiment_id)
    data = payload.model_dump(exclude_unset=True)
    archived = data.pop("archived", None)
    for key, value in data.items():
        setattr(experiment, key, value)
    if archived is not None:
        if archived and experiment.phase not in TERMINAL_PHASES:
            raise ConflictError(
                "Cannot archive experiment while it is "
                f"{experiment.phase.value}; complete or cancel it first"
            )
        experiment.archived_at = datetime.now(UTC) if archived else None
    db.commit()
    db.refresh(experiment)
    return experiment


def soft_delete_experiment(db: Session, experiment_id: uuid.UUID) -> None:
    experiment = get_experiment(db, experiment_id)
    experiment.deleted_at = datetime.now(UTC)
    db.commit()
