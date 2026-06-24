import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent, AgentRole, ExperimentPhase
from server.domain.schemas import (
    AgentCreateResponse,
    AgentRead,
    CommentCreate,
    CommentRead,
    CommentTreeNode,
    ExperimentComplete,
    ExperimentCreate,
    ExperimentDetailRead,
    ExperimentLogCreate,
    ExperimentLogRead,
    ExperimentSummaryRead,
    ExperimentUpdate,
    GlobalStatusRead,
    PlanRevise,
    PlanVersionRead,
    ProjectCreate,
    ProjectRead,
    ProjectStatusRead,
    ProjectStatusRevise,
    ProjectStatusVersionRead,
    ProjectUpdate,
    ReviewCreate,
    ReviewItemRead,
    ReviewItemUpdate,
    ReviewRead,
)
from server.services import auth as auth_service
from server.services import comment_service, log_service, phase_service, plan_service, review_service, status_service
from server.services import permissions as perm
from server.services import project_service as svc
from server.services import project_status_service as status_doc_service
from server.services.errors import ConflictError, ForbiddenError, NotFoundError, StateTransitionError

router = APIRouter(prefix="/projects", tags=["projects"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ForbiddenError):
        return HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, ConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, StateTransitionError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    raise exc


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectRead:
    try:
        perm.require_admin(agent)
        project = svc.create_project(db, payload, author_agent_id=agent.id)
    except ForbiddenError as exc:
        raise _http_error(exc) from exc
    except ConflictError as exc:
        raise _http_error(exc) from exc
    return ProjectRead.model_validate(project)


@router.get("", response_model=list[ProjectRead])
def list_projects(
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[ProjectRead]:
    if perm.is_admin(agent):
        projects = svc.list_projects(db, include_archived=include_archived)
    elif agent.project_id is None:
        projects = []
    else:
        projects = svc.list_projects(
            db, include_archived=include_archived, project_id=agent.project_id
        )
    return [ProjectRead.model_validate(p) for p in projects]


@router.get("/by-key/{project_key}", response_model=ProjectRead)
def get_project_by_key(
    project_key: str,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectRead:
    try:
        project = svc.get_project_by_key(db, project_key)
        perm.ensure_project_access(agent, project.id)
    except NotFoundError as exc:
        raise _http_error(exc) from exc
    except ForbiddenError as exc:
        raise _http_error(exc) from exc
    return ProjectRead.model_validate(project)


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectRead:
    try:
        perm.ensure_project_access(agent, project_id)
        project = svc.get_project(db, project_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise _http_error(exc) from exc
    return ProjectRead.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectRead:
    try:
        perm.require_admin(agent)
        project = svc.update_project(db, project_id, payload)
    except (NotFoundError, ForbiddenError) as exc:
        raise _http_error(exc) from exc
    return ProjectRead.model_validate(project)


@router.get("/{project_id}/status", response_model=ProjectStatusRead)
def get_project_status(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectStatusRead:
    try:
        perm.ensure_project_access(agent, project_id)
        return svc.get_project_status(db, project_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise _http_error(exc) from exc


@router.post(
    "/{project_id}/status/revisions",
    response_model=ProjectStatusVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def revise_project_status(
    project_id: uuid.UUID,
    payload: ProjectStatusRevise,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectStatusVersionRead:
    try:
        perm.require_admin(agent)
        return status_doc_service.revise_status(db, project_id, agent.id, payload)
    except (NotFoundError, ForbiddenError) as exc:
        raise _http_error(exc) from exc


@router.get("/{project_id}/status/versions", response_model=list[ProjectStatusVersionRead])
def list_project_status_versions(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[ProjectStatusVersionRead]:
    try:
        perm.ensure_project_access(agent, project_id)
        return status_doc_service.list_status_versions(db, project_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise _http_error(exc) from exc


@router.get("/{project_id}/status/versions/{version}", response_model=ProjectStatusVersionRead)
def get_project_status_version(
    project_id: uuid.UUID,
    version: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ProjectStatusVersionRead:
    try:
        perm.ensure_project_access(agent, project_id)
        return status_doc_service.get_status_version(db, project_id, version)
    except (NotFoundError, ForbiddenError) as exc:
        raise _http_error(exc) from exc


experiments_router = APIRouter(tags=["experiments"])


@experiments_router.post(
    "/projects/{project_id}/experiments",
    response_model=ExperimentSummaryRead,
    status_code=status.HTTP_201_CREATED,
)
def create_experiment(
    project_id: uuid.UUID,
    payload: ExperimentCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    try:
        resolved_project_id = perm.resolve_project_id_for_agent(agent, project_id)
        perm.ensure_project_access(agent, resolved_project_id)
        experiment = svc.create_experiment(db, resolved_project_id, agent.id, payload)
    except (NotFoundError, ForbiddenError) as exc:
        raise _http_error(exc) from exc
    return ExperimentSummaryRead.model_validate(experiment)


@experiments_router.get("/projects/{project_id}/experiments", response_model=list[ExperimentSummaryRead])
def list_experiments(
    project_id: uuid.UUID,
    phase: ExperimentPhase | None = Query(default=None),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[ExperimentSummaryRead]:
    try:
        resolved_project_id = perm.resolve_project_id_for_agent(agent, project_id)
        perm.ensure_project_access(agent, resolved_project_id)
        experiments = svc.list_experiments(db, resolved_project_id, phase=phase)
    except (NotFoundError, ForbiddenError) as exc:
        raise _http_error(exc) from exc
    return [ExperimentSummaryRead.model_validate(e) for e in experiments]


@experiments_router.get("/experiments/{experiment_id}", response_model=ExperimentDetailRead)
def get_experiment(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentDetailRead:
    try:
        perm.ensure_experiment_access(db, agent, experiment_id)
        return svc.get_experiment_detail(db, experiment_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise _http_error(exc) from exc


@experiments_router.patch("/experiments/{experiment_id}", response_model=ExperimentSummaryRead)
def update_experiment(
    experiment_id: uuid.UUID,
    payload: ExperimentUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    try:
        perm.ensure_experiment_access(db, agent, experiment_id)
        experiment = svc.update_experiment(db, experiment_id, payload)
    except (NotFoundError, ForbiddenError) as exc:
        raise _http_error(exc) from exc
    return ExperimentSummaryRead.model_validate(experiment)


@experiments_router.delete("/experiments/{experiment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_experiment(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> None:
    try:
        perm.ensure_experiment_access(db, agent, experiment_id)
        svc.soft_delete_experiment(db, experiment_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise _http_error(exc) from exc


agents_router = APIRouter(prefix="/agents", tags=["agents"])


@agents_router.post("", response_model=AgentCreateResponse, status_code=status.HTTP_201_CREATED)
def register_agent(
    name: str,
    role: AgentRole = AgentRole.agent,
    project_id: uuid.UUID | None = Query(default=None),
    project_key: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> AgentCreateResponse:
    existing = db.query(Agent).filter(Agent.name == name).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Agent name already exists")

    resolved_project_id: uuid.UUID | None = None
    if role == AgentRole.agent:
        if project_id is not None:
            try:
                resolved_project_id = svc.get_project(db, project_id).id
            except NotFoundError as exc:
                raise _http_error(exc) from exc
        elif project_key is not None:
            try:
                resolved_project_id = svc.get_project_by_key(db, project_key).id
            except NotFoundError as exc:
                raise _http_error(exc) from exc
        else:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="project_id or project_key is required for role=agent",
            )

    try:
        agent, token = auth_service.create_agent(db, name, role, project_id=resolved_project_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AgentCreateResponse(
        id=agent.id,
        name=agent.name,
        role=agent.role,
        project_id=agent.project_id,
        created_at=agent.created_at,
        api_token=token,
    )


@agents_router.get("/me", response_model=AgentRead)
def get_me(
    agent: Agent = Depends(get_current_agent),
    db: Session = Depends(get_db),
) -> AgentRead:
    project_key: str | None = None
    if agent.project_id is not None:
        project = svc.get_project(db, agent.project_id)
        project_key = project.project_key
    return AgentRead(
        id=agent.id,
        name=agent.name,
        role=agent.role,
        project_id=agent.project_id,
        project_key=project_key,
        created_at=agent.created_at,
    )


# --- M2: phase transitions ---


@experiments_router.post("/experiments/{experiment_id}/submit-review", response_model=ExperimentSummaryRead)
def submit_for_review(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    try:
        perm.ensure_experiment_access(db, agent, experiment_id)
        phase_service.submit_for_review(db, experiment_id, agent)
        experiment = svc.get_experiment(db, experiment_id)
    except (NotFoundError, ForbiddenError, StateTransitionError) as exc:
        raise _http_error(exc) from exc
    return ExperimentSummaryRead.model_validate(experiment)


@experiments_router.post("/experiments/{experiment_id}/approve", response_model=ExperimentSummaryRead)
def approve_experiment(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    try:
        perm.ensure_experiment_access(db, agent, experiment_id)
        phase_service.approve_experiment(db, experiment_id, agent)
        experiment = svc.get_experiment(db, experiment_id)
    except (NotFoundError, ForbiddenError, StateTransitionError) as exc:
        raise _http_error(exc) from exc
    return ExperimentSummaryRead.model_validate(experiment)


@experiments_router.post("/experiments/{experiment_id}/withdraw", response_model=ExperimentSummaryRead)
def withdraw_from_review(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    try:
        perm.ensure_experiment_access(db, agent, experiment_id)
        phase_service.withdraw_from_review(db, experiment_id, agent)
        experiment = svc.get_experiment(db, experiment_id)
    except (NotFoundError, ForbiddenError, StateTransitionError) as exc:
        raise _http_error(exc) from exc
    return ExperimentSummaryRead.model_validate(experiment)


@experiments_router.post("/experiments/{experiment_id}/cancel", response_model=ExperimentSummaryRead)
def cancel_experiment(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    try:
        perm.ensure_experiment_access(db, agent, experiment_id)
        phase_service.cancel_experiment(db, experiment_id, agent)
        experiment = svc.get_experiment(db, experiment_id)
    except (NotFoundError, ForbiddenError, StateTransitionError) as exc:
        raise _http_error(exc) from exc
    return ExperimentSummaryRead.model_validate(experiment)


# --- M2: plans ---


@experiments_router.get("/experiments/{experiment_id}/plans", response_model=list[PlanVersionRead])
def list_plans(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[PlanVersionRead]:
    try:
        perm.ensure_experiment_access(db, agent, experiment_id)
        plans = plan_service.list_plans(db, experiment_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise _http_error(exc) from exc
    return [PlanVersionRead.model_validate(p) for p in plans]


@experiments_router.get("/experiments/{experiment_id}/plans/{version}", response_model=PlanVersionRead)
def get_plan_version(
    experiment_id: uuid.UUID,
    version: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> PlanVersionRead:
    try:
        perm.ensure_experiment_access(db, agent, experiment_id)
        plan = plan_service.get_plan_version(db, experiment_id, version)
    except (NotFoundError, ForbiddenError) as exc:
        raise _http_error(exc) from exc
    return PlanVersionRead.model_validate(plan)


@experiments_router.post(
    "/experiments/{experiment_id}/plans",
    response_model=PlanVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def revise_plan(
    experiment_id: uuid.UUID,
    payload: PlanRevise,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> PlanVersionRead:
    try:
        perm.ensure_experiment_access(db, agent, experiment_id)
        plan = plan_service.revise_plan(db, experiment_id, agent, payload)
    except (NotFoundError, ForbiddenError, StateTransitionError) as exc:
        raise _http_error(exc) from exc
    return PlanVersionRead.model_validate(plan)


# --- M2: reviews ---


@experiments_router.post(
    "/experiments/{experiment_id}/reviews",
    response_model=ReviewRead,
    status_code=status.HTTP_201_CREATED,
)
def create_review(
    experiment_id: uuid.UUID,
    payload: ReviewCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ReviewRead:
    try:
        perm.ensure_experiment_access(db, agent, experiment_id)
        review = review_service.create_review(db, experiment_id, agent, payload)
    except (NotFoundError, ForbiddenError, ConflictError, StateTransitionError) as exc:
        raise _http_error(exc) from exc
    return review_service.review_to_read(review)


@experiments_router.get("/experiments/{experiment_id}/reviews", response_model=list[ReviewRead])
def list_reviews(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[ReviewRead]:
    try:
        perm.ensure_experiment_access(db, agent, experiment_id)
        reviews = review_service.list_reviews(db, experiment_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise _http_error(exc) from exc
    return [review_service.review_to_read(r) for r in reviews]


@experiments_router.patch("/review-items/{item_id}", response_model=ReviewItemRead)
def update_review_item(
    item_id: uuid.UUID,
    payload: ReviewItemUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ReviewItemRead:
    try:
        perm.ensure_review_item_access(db, agent, item_id)
        item = review_service.update_review_item(db, item_id, agent, payload)
    except (NotFoundError, ForbiddenError, StateTransitionError) as exc:
        raise _http_error(exc) from exc
    return ReviewItemRead.model_validate(item)


# --- M2: comments ---


@experiments_router.post(
    "/experiments/{experiment_id}/comments",
    response_model=CommentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_comment(
    experiment_id: uuid.UUID,
    payload: CommentCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> CommentRead:
    try:
        perm.ensure_experiment_access(db, agent, experiment_id)
        comment = comment_service.create_comment(db, experiment_id, agent, payload)
    except (NotFoundError, ForbiddenError) as exc:
        raise _http_error(exc) from exc
    return CommentRead.model_validate(comment)


@experiments_router.get("/experiments/{experiment_id}/comments")
def list_comments(
    experiment_id: uuid.UUID,
    tree: bool = Query(default=False),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[CommentRead] | list[CommentTreeNode]:
    try:
        perm.ensure_experiment_access(db, agent, experiment_id)
        comments = comment_service.list_comments(db, experiment_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise _http_error(exc) from exc
    if tree:
        return comment_service.build_comment_tree(comments)
    return [CommentRead.model_validate(c) for c in comments]


status_router = APIRouter(prefix="/status", tags=["status"])


@status_router.get("", response_model=GlobalStatusRead)
def get_global_status(
    project_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> GlobalStatusRead:
    try:
        if project_id is None:
            if not perm.is_admin(agent):
                if agent.project_id is None:
                    raise ForbiddenError("Agent is not bound to a project")
                project_id = agent.project_id
            else:
                return status_service.get_global_status(db, project_id=None)
        perm.ensure_project_access(agent, project_id)
        return status_service.get_global_status(db, project_id=project_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise _http_error(exc) from exc


# --- M3: execution ---


@experiments_router.post("/experiments/{experiment_id}/start", response_model=ExperimentSummaryRead)
def start_experiment(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    try:
        perm.ensure_experiment_access(db, agent, experiment_id)
        phase_service.start_experiment(db, experiment_id, agent)
        experiment = svc.get_experiment(db, experiment_id)
    except (NotFoundError, ForbiddenError, StateTransitionError) as exc:
        raise _http_error(exc) from exc
    return ExperimentSummaryRead.model_validate(experiment)


@experiments_router.post("/experiments/{experiment_id}/complete", response_model=ExperimentSummaryRead)
def complete_experiment(
    experiment_id: uuid.UUID,
    payload: ExperimentComplete,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    try:
        perm.ensure_experiment_access(db, agent, experiment_id)
        phase_service.complete_experiment(db, experiment_id, agent, payload)
        experiment = svc.get_experiment(db, experiment_id)
    except (NotFoundError, ForbiddenError, StateTransitionError) as exc:
        raise _http_error(exc) from exc
    return ExperimentSummaryRead.model_validate(experiment)


@experiments_router.post(
    "/experiments/{experiment_id}/logs",
    response_model=ExperimentLogRead,
    status_code=status.HTTP_201_CREATED,
)
def create_log(
    experiment_id: uuid.UUID,
    payload: ExperimentLogCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentLogRead:
    try:
        perm.ensure_experiment_access(db, agent, experiment_id)
        log = log_service.create_log(db, experiment_id, agent, payload)
    except (NotFoundError, ForbiddenError, StateTransitionError) as exc:
        raise _http_error(exc) from exc
    return ExperimentLogRead.model_validate(log)


@experiments_router.get("/experiments/{experiment_id}/logs", response_model=list[ExperimentLogRead])
def list_logs(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[ExperimentLogRead]:
    try:
        perm.ensure_experiment_access(db, agent, experiment_id)
        logs = log_service.list_logs(db, experiment_id)
    except (NotFoundError, ForbiddenError) as exc:
        raise _http_error(exc) from exc
    return [ExperimentLogRead.model_validate(log) for log in logs]
