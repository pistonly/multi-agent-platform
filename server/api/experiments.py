import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from server.api.background_tasks import bind_background_tasks
from server.api.common import emit
from server.api.deps import get_current_agent
from server.api.experiment_execution import execution_router
from server.api.experiment_reviews import reviews_router
from server.auth import experiment_access
from server.db.session import get_db
from server.domain.models import Agent, ExperimentPhase, Project
from server.domain.schemas import (
    ExperimentBundleRead,
    ExperimentCreate,
    ExperimentDetailRead,
    ExperimentSummaryRead,
    ExperimentUpdate,
)
from server.services import notification_service, phase_service
from server.services import permissions as perm
from server.services import project_service as svc
from server.services.experiment_capabilities_service import experiment_summary_for_actor


def _summary_for_agent(db: Session, experiment, agent: Agent, **extra) -> ExperimentSummaryRead:
    return experiment_summary_for_actor(db, experiment, agent, extra_updates=extra or None)


# T17（2026-08）：路由按域拆分为 execution / reviews 两个子模块，此处仅保留
# CRUD 与相位流转，并聚合挂载——对外 URL 契约与 ``experiments_router``
# 导入路径不变。
experiments_router = APIRouter(tags=["experiments"], dependencies=[Depends(bind_background_tasks)])
experiments_router.include_router(execution_router)
experiments_router.include_router(reviews_router)


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
    resolved_project_id = perm.resolve_project_id_for_agent(agent, project_id)
    perm.ensure_project_access(agent, resolved_project_id)
    warnings = svc.create_experiment_warnings(db, resolved_project_id, payload.topic_id)
    experiment = svc.create_experiment(db, resolved_project_id, agent.id, payload)
    emit(
        db,
        agent,
        action="experiment.created",
        target_type="experiment",
        target_id=experiment.id,
        project_id=resolved_project_id,
        summary=f"创建实验「{experiment.title}」",
        event="experiment.created",
        event_payload={"id": str(experiment.id), "title": experiment.title},
    )
    return _summary_for_agent(db, experiment, agent, warnings=warnings)


@experiments_router.get("/projects/{project_id}/experiments", response_model=list[ExperimentSummaryRead])
def list_experiments(
    project_id: uuid.UUID,
    response: Response,
    phase: ExperimentPhase | None = Query(default=None),
    creator_agent_id: uuid.UUID | None = Query(default=None),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    # cleanup experiment (f12a5638) Exp B: unified ``limit`` query param
    # (default 50). ``page_size`` is kept as a deprecated alias for one
    # minor version; when only ``page_size`` is sent we honour it AND
    # surface ``Deprecation`` + ``Sunset`` response headers so clients
    # migrate before v0.12 (where ``page_size`` will be removed).
    limit: int | None = Query(default=None, ge=1, le=100),
    page_size: int | None = Query(default=None, ge=1, le=100),
    include_archived: bool = Query(default=False),
    # v0.12 M54B (E2): short-id prefix resolution at the DB layer —
    # ``CAST(id AS CHAR) LIKE '<prefix>%'``. 8..32 hex chars; a full
    # 36-char UUID should just hit GET /experiments/{id}.
    id_prefix: str | None = Query(default=None, min_length=8, max_length=32, pattern=r"^[0-9a-fA-F]+$"),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[ExperimentSummaryRead]:
    resolved_project_id = perm.resolve_project_id_for_agent(agent, project_id)
    perm.ensure_project_access(agent, resolved_project_id)

    # Resolve effective page_size: limit wins; else page_size (deprecated);
    # else default 50.
    if limit is not None:
        effective_page_size = limit
    elif page_size is not None:
        effective_page_size = page_size
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = "v0.12"
        response.headers["Link"] = (
            f'<{"?limit=" + str(page_size)}>; rel="successor-version"'
        )
    else:
        effective_page_size = 50

    experiments, total = svc.list_experiments(
        db,
        resolved_project_id,
        phase=phase,
        creator_agent_id=creator_agent_id,
        q=q,
        page=page,
        page_size=effective_page_size,
        include_archived=include_archived,
        id_prefix=id_prefix,
    )
    response.headers["X-Total-Count"] = str(total)
    project = db.get(Project, resolved_project_id)
    source = None
    if project is not None:
        from server.services.fs_source_service import content_source_meta

        source = content_source_meta(db, project)
    return [
        ExperimentSummaryRead.model_validate(e).model_copy(update={"source": source})
        for e in experiments
    ]


@experiments_router.get("/experiments/{experiment_id}", response_model=ExperimentDetailRead)
def get_experiment(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentDetailRead:
    perm.ensure_experiment_access(db, agent, experiment_id)
    return svc.get_experiment_detail(db, experiment_id, agent)


@experiments_router.get("/experiments/{experiment_id}/bundle", response_model=ExperimentBundleRead)
def get_experiment_bundle(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentBundleRead:
    perm.ensure_experiment_access(db, agent, experiment_id)
    return svc.get_experiment_bundle(db, experiment_id, agent)


@experiments_router.patch("/experiments/{experiment_id}", response_model=ExperimentSummaryRead)
def update_experiment(
    experiment_id: uuid.UUID,
    payload: ExperimentUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    # authz PR3: PATCH is a non-state-machine mutation but still
    # touches experiment metadata (title/description). Only the
    # creator or an admin should be able to rename someone else's
    # experiment — the project-level guard alone was too loose.
    experiment_access.ensure_experiment_creator_or_admin(db, agent, experiment_id)
    experiment = svc.update_experiment(db, experiment_id, payload)
    return _summary_for_agent(db, experiment, agent)


@experiments_router.delete("/experiments/{experiment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_experiment(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> None:
    # authz PR3: same rationale as update_experiment — soft-delete is
    # a destructive mutation, creator/admin only.
    experiment_access.ensure_experiment_creator_or_admin(db, agent, experiment_id)
    svc.soft_delete_experiment(db, experiment_id)


# --- M2: phase transitions ---


@experiments_router.post("/experiments/{experiment_id}/submit-review", response_model=ExperimentSummaryRead)
def submit_for_review(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    perm.ensure_experiment_access(db, agent, experiment_id)
    phase_service.submit_for_review(db, experiment_id, agent)
    experiment = svc.get_experiment(db, experiment_id)
    emit(
        db,
        agent,
        action="experiment.phase_changed",
        target_type="experiment",
        target_id=experiment_id,
        project_id=experiment.project_id,
        summary=f"提交评审（{experiment.title}）",
        event="experiment.phase_changed",
        event_payload={"id": str(experiment_id), "phase": experiment.phase.value, "title": experiment.title},
    )
    return _summary_for_agent(db, experiment, agent)


@experiments_router.post("/experiments/{experiment_id}/approve", response_model=ExperimentSummaryRead)
def approve_experiment(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    perm.ensure_experiment_access(db, agent, experiment_id)
    phase_service.approve_experiment(db, experiment_id, agent)
    experiment = svc.get_experiment(db, experiment_id)
    emit(
        db,
        agent,
        action="experiment.phase_changed",
        target_type="experiment",
        target_id=experiment_id,
        project_id=experiment.project_id,
        summary=f"批准实验（{experiment.title}）",
        event="experiment.phase_changed",
        event_payload={"id": str(experiment_id), "phase": experiment.phase.value, "title": experiment.title},
    )
    return _summary_for_agent(db, experiment, agent)


@experiments_router.post("/experiments/{experiment_id}/withdraw", response_model=ExperimentSummaryRead)
def withdraw_from_review(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    perm.ensure_experiment_access(db, agent, experiment_id)
    phase_service.withdraw_from_review(db, experiment_id, agent)
    experiment = svc.get_experiment(db, experiment_id)
    # Phase 2 D2: kind-directed SSE so the waker can map to ``experiment_lifecycle``.
    notification_service.emit_kind(
        db,
        project_id=experiment.project_id,
        actor_id=agent.id,
        personas=["host", "reviewer"],
        event="experiment.lifecycle.withdrawn",
        summary=f"实验已撤回评审（{experiment.title}）",
        target_type="experiment",
        target_id=experiment.id,
        payload={"experiment_id": str(experiment.id), "title": experiment.title, "phase": experiment.phase.value},
    )
    return _summary_for_agent(db, experiment, agent)


@experiments_router.post("/experiments/{experiment_id}/cancel", response_model=ExperimentSummaryRead)
def cancel_experiment(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    perm.ensure_experiment_access(db, agent, experiment_id)
    phase_service.cancel_experiment(db, experiment_id, agent)
    experiment = svc.get_experiment(db, experiment_id)
    # Phase 2 D2: kind-directed SSE so the waker can map to ``experiment_lifecycle``.
    notification_service.emit_kind(
        db,
        project_id=experiment.project_id,
        actor_id=agent.id,
        personas=["host", "reviewer"],
        event="experiment.lifecycle.cancelled",
        summary=f"实验已取消（{experiment.title}）",
        target_type="experiment",
        target_id=experiment.id,
        payload={"experiment_id": str(experiment.id), "title": experiment.title, "phase": experiment.phase.value},
    )
    return _summary_for_agent(db, experiment, agent)
