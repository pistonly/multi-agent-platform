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
    ProjectUpdate,
    ReviewCreate,
    ReviewItemRead,
    ReviewItemUpdate,
    ReviewRead,
)
from server.services import auth as auth_service
from server.services import comment_service, log_service, phase_service, plan_service, review_service, status_service
from server.services import project_service as svc
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
    _: Agent = Depends(get_current_agent),
) -> ProjectRead:
    project = svc.create_project(db, payload)
    return ProjectRead.model_validate(project)


@router.get("", response_model=list[ProjectRead])
def list_projects(
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
    _: Agent = Depends(get_current_agent),
) -> list[ProjectRead]:
    projects = svc.list_projects(db, include_archived=include_archived)
    return [ProjectRead.model_validate(p) for p in projects]


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Agent = Depends(get_current_agent),
) -> ProjectRead:
    try:
        project = svc.get_project(db, project_id)
    except NotFoundError as exc:
        raise _http_error(exc) from exc
    return ProjectRead.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    _: Agent = Depends(get_current_agent),
) -> ProjectRead:
    try:
        project = svc.update_project(db, project_id, payload)
    except NotFoundError as exc:
        raise _http_error(exc) from exc
    return ProjectRead.model_validate(project)


@router.get("/{project_id}/status", response_model=ProjectStatusRead)
def get_project_status(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Agent = Depends(get_current_agent),
) -> ProjectStatusRead:
    try:
        return svc.get_project_status(db, project_id)
    except NotFoundError as exc:
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
        experiment = svc.create_experiment(db, project_id, agent.id, payload)
    except NotFoundError as exc:
        raise _http_error(exc) from exc
    return ExperimentSummaryRead.model_validate(experiment)


@experiments_router.get("/projects/{project_id}/experiments", response_model=list[ExperimentSummaryRead])
def list_experiments(
    project_id: uuid.UUID,
    phase: ExperimentPhase | None = Query(default=None),
    db: Session = Depends(get_db),
    _: Agent = Depends(get_current_agent),
) -> list[ExperimentSummaryRead]:
    try:
        experiments = svc.list_experiments(db, project_id, phase=phase)
    except NotFoundError as exc:
        raise _http_error(exc) from exc
    return [ExperimentSummaryRead.model_validate(e) for e in experiments]


@experiments_router.get("/experiments/{experiment_id}", response_model=ExperimentDetailRead)
def get_experiment(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Agent = Depends(get_current_agent),
) -> ExperimentDetailRead:
    try:
        return svc.get_experiment_detail(db, experiment_id)
    except NotFoundError as exc:
        raise _http_error(exc) from exc


@experiments_router.patch("/experiments/{experiment_id}", response_model=ExperimentSummaryRead)
def update_experiment(
    experiment_id: uuid.UUID,
    payload: ExperimentUpdate,
    db: Session = Depends(get_db),
    _: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    try:
        experiment = svc.update_experiment(db, experiment_id, payload)
    except NotFoundError as exc:
        raise _http_error(exc) from exc
    return ExperimentSummaryRead.model_validate(experiment)


@experiments_router.delete("/experiments/{experiment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_experiment(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Agent = Depends(get_current_agent),
) -> None:
    try:
        svc.soft_delete_experiment(db, experiment_id)
    except NotFoundError as exc:
        raise _http_error(exc) from exc


agents_router = APIRouter(prefix="/agents", tags=["agents"])


@agents_router.post("", response_model=AgentCreateResponse, status_code=status.HTTP_201_CREATED)
def register_agent(
    name: str,
    role: AgentRole = AgentRole.agent,
    db: Session = Depends(get_db),
) -> AgentCreateResponse:
    existing = db.query(Agent).filter(Agent.name == name).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Agent name already exists")
    agent, token = auth_service.create_agent(db, name, role)
    return AgentCreateResponse(
        id=agent.id,
        name=agent.name,
        role=agent.role,
        created_at=agent.created_at,
        api_token=token,
    )


@agents_router.get("/me", response_model=AgentRead)
def get_me(agent: Agent = Depends(get_current_agent)) -> AgentRead:
    return AgentRead.model_validate(agent)


# --- M2: phase transitions ---


@experiments_router.post("/experiments/{experiment_id}/submit-review", response_model=ExperimentSummaryRead)
def submit_for_review(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    try:
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
    _: Agent = Depends(get_current_agent),
) -> list[PlanVersionRead]:
    try:
        plans = plan_service.list_plans(db, experiment_id)
    except NotFoundError as exc:
        raise _http_error(exc) from exc
    return [PlanVersionRead.model_validate(p) for p in plans]


@experiments_router.get("/experiments/{experiment_id}/plans/{version}", response_model=PlanVersionRead)
def get_plan_version(
    experiment_id: uuid.UUID,
    version: int,
    db: Session = Depends(get_db),
    _: Agent = Depends(get_current_agent),
) -> PlanVersionRead:
    try:
        plan = plan_service.get_plan_version(db, experiment_id, version)
    except NotFoundError as exc:
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
        review = review_service.create_review(db, experiment_id, agent, payload)
    except (NotFoundError, ConflictError, StateTransitionError) as exc:
        raise _http_error(exc) from exc
    return review_service.review_to_read(review)


@experiments_router.get("/experiments/{experiment_id}/reviews", response_model=list[ReviewRead])
def list_reviews(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Agent = Depends(get_current_agent),
) -> list[ReviewRead]:
    try:
        reviews = review_service.list_reviews(db, experiment_id)
    except NotFoundError as exc:
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
        item = review_service.update_review_item(db, item_id, agent, payload)
    except (NotFoundError, StateTransitionError) as exc:
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
        comment = comment_service.create_comment(db, experiment_id, agent, payload)
    except NotFoundError as exc:
        raise _http_error(exc) from exc
    return CommentRead.model_validate(comment)


@experiments_router.get("/experiments/{experiment_id}/comments")
def list_comments(
    experiment_id: uuid.UUID,
    tree: bool = Query(default=False),
    db: Session = Depends(get_db),
    _: Agent = Depends(get_current_agent),
) -> list[CommentRead] | list[CommentTreeNode]:
    try:
        comments = comment_service.list_comments(db, experiment_id)
    except NotFoundError as exc:
        raise _http_error(exc) from exc
    if tree:
        return comment_service.build_comment_tree(comments)
    return [CommentRead.model_validate(c) for c in comments]


status_router = APIRouter(prefix="/status", tags=["status"])


@status_router.get("", response_model=GlobalStatusRead)
def get_global_status(
    project_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    _: Agent = Depends(get_current_agent),
) -> GlobalStatusRead:
    try:
        return status_service.get_global_status(db, project_id=project_id)
    except NotFoundError as exc:
        raise _http_error(exc) from exc


# --- M3: execution ---


@experiments_router.post("/experiments/{experiment_id}/start", response_model=ExperimentSummaryRead)
def start_experiment(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    try:
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
        log = log_service.create_log(db, experiment_id, agent, payload)
    except (NotFoundError, StateTransitionError) as exc:
        raise _http_error(exc) from exc
    return ExperimentLogRead.model_validate(log)


@experiments_router.get("/experiments/{experiment_id}/logs", response_model=list[ExperimentLogRead])
def list_logs(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Agent = Depends(get_current_agent),
) -> list[ExperimentLogRead]:
    try:
        logs = log_service.list_logs(db, experiment_id)
    except NotFoundError as exc:
        raise _http_error(exc) from exc
    return [ExperimentLogRead.model_validate(log) for log in logs]
