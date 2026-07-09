import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from server.api.background_tasks import bind_background_tasks
from server.api.common import emit
from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent, ExperimentPhase
from server.domain.schemas import (
    AuditLogRead,
    CommentCreate,
    CommentRead,
    CommentTreeNode,
    CrossPersonaCallRecord,
    EvidenceValidationSchema,
    EvidenceWarningSchema,
    ExperimentBundleRead,
    ExperimentComplete,
    ExperimentCreate,
    ExperimentDetailRead,
    ExperimentLockRead,
    ExperimentLockStalledScanRead,
    ExperimentLogCreate,
    ExperimentLogRead,
    ExperimentResultDecision,
    ExperimentSummaryRead,
    ExperimentUpdate,
    LogCreateResponse,
    PlanRevise,
    PlanVersionRead,
    ReviewCreate,
    ReviewItemRead,
    ReviewItemUpdate,
    ReviewRead,
    SimilarityWarningSchema,
    TemplateValidationSchema,
    TemplateWarningSchema,
)
from server.services import (
    audit_service,
    comment_service,
    lock_service,
    log_service,
    notification_service,
    phase_service,
    plan_service,
    review_service,
)
from server.services import permissions as perm
from server.services import project_service as svc
from server.auth import experiment_access
from server.services.errors import ForbiddenError
from server.services.experiment_capabilities_service import experiment_summary_for_actor
from server.services.template_service import validate_result_submission_template

HOST_AGENT_NAME = "multi-agents-platform-host"


def _summary_for_agent(db: Session, experiment, agent: Agent, **extra) -> ExperimentSummaryRead:
    return experiment_summary_for_actor(db, experiment, agent, extra_updates=extra or None)

experiments_router = APIRouter(tags=["experiments"], dependencies=[Depends(bind_background_tasks)])


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
    )
    response.headers["X-Total-Count"] = str(total)
    return [ExperimentSummaryRead.model_validate(e) for e in experiments]


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
    perm.ensure_experiment_access(db, agent, experiment_id)
    experiment = svc.update_experiment(db, experiment_id, payload)
    return _summary_for_agent(db, experiment, agent)


@experiments_router.delete("/experiments/{experiment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_experiment(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> None:
    perm.ensure_experiment_access(db, agent, experiment_id)
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


# --- M2: plans ---


@experiments_router.get("/experiments/{experiment_id}/plans", response_model=list[PlanVersionRead])
def list_plans(
    experiment_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[PlanVersionRead]:
    perm.ensure_experiment_access(db, agent, experiment_id)
    plans = plan_service.list_plans(db, experiment_id, limit=limit)
    return [PlanVersionRead.model_validate(p) for p in plans]


@experiments_router.get("/experiments/{experiment_id}/plans/{version}", response_model=PlanVersionRead)
def get_plan_version(
    experiment_id: uuid.UUID,
    version: int,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> PlanVersionRead:
    perm.ensure_experiment_access(db, agent, experiment_id)
    plan = plan_service.get_plan_version(db, experiment_id, version)
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
    experiment = perm.ensure_experiment_access(db, agent, experiment_id)
    previous_version = experiment.current_plan_version
    plan = plan_service.revise_plan(db, experiment_id, agent, payload)
    if plan.version != previous_version:
        emit(
            db,
            agent,
            action="plan.revised",
            target_type="plan_version",
            target_id=plan.id,
            project_id=experiment.project_id,
            summary=f"修订计划 v{plan.version}",
            event="plan.revised",
            event_payload={"experiment_id": str(experiment_id), "version": plan.version},
        )
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
    experiment = perm.ensure_experiment_access(db, agent, experiment_id)
    review = review_service.create_review(db, experiment_id, agent, payload)
    emit(
        db,
        agent,
        action="review.submitted",
        target_type="review",
        target_id=review.id,
        project_id=experiment.project_id,
        summary="提交评审",
        event="review.submitted",
        event_payload={"experiment_id": str(experiment_id), "review_id": str(review.id)},
    )
    return review_service.review_to_read(db, review)


@experiments_router.get("/experiments/{experiment_id}/reviews", response_model=list[ReviewRead])
def list_reviews(
    experiment_id: uuid.UUID,
    include_archived: bool = Query(
        default=True,
        description=(
            "Whether to include archived reviews. Defaults to true for N=2 "
            "transition (legacy e2e tests + UI initial load expect to see all); "
            "set false to filter to active reviews only."
        ),
    ),
    plan_version: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[ReviewRead]:
    perm.ensure_experiment_access(db, agent, experiment_id)
    reviews = review_service.list_reviews(
        db,
        experiment_id,
        include_archived=include_archived,
        plan_version=plan_version,
        limit=limit,
    )
    return [review_service.review_to_read(db, r) for r in reviews]


@experiments_router.post(
    "/experiments/{experiment_id}/reviews/{review_id}/withdraw",
    status_code=status.HTTP_204_NO_CONTENT,
)
def withdraw_review(
    experiment_id: uuid.UUID,
    review_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> None:
    experiment = perm.ensure_experiment_access(db, agent, experiment_id)
    review_service.withdraw_review(db, experiment_id, review_id, agent)
    emit(
        db,
        agent,
        action="review.withdrawn",
        target_type="review",
        target_id=review_id,
        project_id=experiment.project_id,
        summary="撤回评审",
        event="review.withdrawn",
        event_payload={"experiment_id": str(experiment_id), "review_id": str(review_id)},
    )


@experiments_router.patch("/review-items/{item_id}", response_model=ReviewItemRead)
def update_review_item(
    item_id: uuid.UUID,
    payload: ReviewItemUpdate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ReviewItemRead:
    perm.ensure_review_item_access(db, agent, item_id)
    item = review_service.update_review_item(db, item_id, agent, payload)
    # Phase 2 D2: kind-directed SSE for the addressed/responded/rebutted
    # transition so the waker can map to ``addressed_review_item``.
    experiment = svc.get_experiment(db, item.review.experiment_id)
    notification_service.emit_kind(
        db,
        project_id=experiment.project_id,
        actor_id=agent.id,
        personas=["host"],
        event="review_item.status_changed",
        summary=f"评审项状态变为 {item.status.value if item.status else 'updated'}（{experiment.title}）",
        target_type="review_item",
        target_id=item.id,
        payload={
            "experiment_id": str(experiment.id),
            "review_id": str(item.review_id),
            "item_id": str(item.id),
            "status": item.status.value if item.status else None,
        },
    )
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
    experiment = perm.ensure_experiment_access(db, agent, experiment_id)
    comment, unresolved = comment_service.create_comment(db, experiment_id, agent, payload)
    emit(
        db,
        agent,
        action="comment.created",
        target_type="comment",
        target_id=comment.id,
        project_id=experiment.project_id,
        summary="发表评论",
        event="comment.created",
        event_payload={"experiment_id": str(experiment_id), "comment_id": str(comment.id)},
    )
    return comment_service.comment_read(db, comment, unresolved_mentions=unresolved)


@experiments_router.get("/experiments/{experiment_id}/comments")
def list_comments(
    experiment_id: uuid.UUID,
    tree: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[CommentRead] | list[CommentTreeNode]:
    perm.ensure_experiment_access(db, agent, experiment_id)
    comments = comment_service.list_comments(db, experiment_id, limit=limit)
    if tree:
        return comment_service.build_comment_tree(db, comments)
    return comment_service.comments_to_read(db, comments)


# --- M3: execution ---


@experiments_router.post("/experiments/{experiment_id}/start", response_model=ExperimentSummaryRead)
def start_experiment(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    perm.ensure_experiment_access(db, agent, experiment_id)
    phase_service.start_experiment(db, experiment_id, agent)
    experiment = svc.get_experiment(db, experiment_id)
    emit(
        db,
        agent,
        action="experiment.phase_changed",
        target_type="experiment",
        target_id=experiment_id,
        project_id=experiment.project_id,
        summary=f"开始执行（{experiment.title}）",
        event="experiment.phase_changed",
        event_payload={"id": str(experiment_id), "phase": experiment.phase.value, "title": experiment.title},
    )
    return _summary_for_agent(db, experiment, agent)


@experiments_router.post("/experiments/{experiment_id}/complete", response_model=ExperimentSummaryRead)
def complete_experiment(
    experiment_id: uuid.UUID,
    payload: ExperimentComplete,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    perm.ensure_experiment_access(db, agent, experiment_id)
    phase_service.complete_experiment(db, experiment_id, agent, payload)
    experiment = svc.get_experiment(db, experiment_id)
    # b72d0542 I1.b: 4-段 template soft validation. Mirrors evidence
    # validation: never blocks complete; surfaced via response for
    # reviewer + host to triage.
    template_result = validate_result_submission_template(payload.content_md)
    template_validation = TemplateValidationSchema(
        warnings=[
            TemplateWarningSchema(code=w.code, section=w.section, detail=w.detail)
            for w in template_result.warnings
        ],
        sections_present=list(template_result.sections_present),
        log_link_count=template_result.log_link_count,
        valid=template_result.valid,
    )
    emit(
        db,
        agent,
        action="experiment.phase_changed",
        target_type="experiment",
        target_id=experiment_id,
        project_id=experiment.project_id,
        summary=f"提交实验结果待审批（{experiment.title}）",
        event="experiment.phase_changed",
        event_payload={"id": str(experiment_id), "phase": experiment.phase.value, "title": experiment.title},
    )
    return _summary_for_agent(
        db, experiment, agent, template_validation=template_validation
    )


@experiments_router.post("/experiments/{experiment_id}/accept-result", response_model=ExperimentSummaryRead)
def accept_experiment_result(
    experiment_id: uuid.UUID,
    payload: ExperimentResultDecision,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    perm.ensure_experiment_access(db, agent, experiment_id)
    phase_service.accept_result(db, experiment_id, agent, payload)
    experiment = svc.get_experiment(db, experiment_id)
    emit(
        db,
        agent,
        action="experiment.phase_changed",
        target_type="experiment",
        target_id=experiment_id,
        project_id=experiment.project_id,
        summary=f"审批通过实验结果（{experiment.title}）",
        event="experiment.phase_changed",
        event_payload={"id": str(experiment_id), "phase": experiment.phase.value, "title": experiment.title},
    )
    return _summary_for_agent(db, experiment, agent)


@experiments_router.post("/experiments/{experiment_id}/reject-result", response_model=ExperimentSummaryRead)
def reject_experiment_result(
    experiment_id: uuid.UUID,
    payload: ExperimentResultDecision,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    perm.ensure_experiment_access(db, agent, experiment_id)
    phase_service.reject_result(db, experiment_id, agent, payload)
    experiment = svc.get_experiment(db, experiment_id)
    emit(
        db,
        agent,
        action="experiment.phase_changed",
        target_type="experiment",
        target_id=experiment_id,
        project_id=experiment.project_id,
        summary=f"驳回实验结果（{experiment.title}）",
        event="experiment.phase_changed",
        event_payload={"id": str(experiment_id), "phase": experiment.phase.value, "title": experiment.title},
    )
    return _summary_for_agent(db, experiment, agent)


@experiments_router.post(
    "/experiments/{experiment_id}/logs",
    response_model=LogCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_log(
    experiment_id: uuid.UUID,
    payload: ExperimentLogCreate,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> LogCreateResponse:
    perm.ensure_experiment_access(db, agent, experiment_id)
    log, validation, similarity, force_skip_applied = log_service.create_log(
        db, experiment_id, agent, payload
    )
    validation_schema = EvidenceValidationSchema(
        warnings=[
            EvidenceWarningSchema(
                code=w.code,
                missing_key=w.missing_key,
                plan_required=w.plan_required,
                log_provided=w.log_provided,
            )
            for w in validation.warnings
        ],
        parse_error=validation.parse_error,
        plan_keys=list(validation.plan_keys),
        valid=validation.valid,
    )
    # b72d0542 I1.b(2)(e): surface similarity warning unless caller
    # acknowledged via ``force_skip_similarity=True`` (the audit row
    # already landed in ``log_service.append_log``). ``similarity_warning``
    # is None when no warning fires; ``force_skip`` echoes the
    # acknowledgement for downstream consumers.
    similarity_warning: SimilarityWarningSchema | None = None
    if similarity.warnings and not force_skip_applied:
        warning = similarity.warnings[0]
        similarity_warning = SimilarityWarningSchema(
            code=warning.code,
            score=warning.score,
            threshold=warning.threshold,
            ref_log_id=warning.ref_log_id,
            model=warning.model,
        )
    return LogCreateResponse(
        log=ExperimentLogRead.model_validate(log),
        validation=validation_schema,
        similarity_warning=similarity_warning,
        force_skip=force_skip_applied,
    )


@experiments_router.get("/experiments/{experiment_id}/logs", response_model=list[ExperimentLogRead])
def list_logs(
    experiment_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[ExperimentLogRead]:
    perm.ensure_experiment_access(db, agent, experiment_id)
    logs = log_service.list_logs(db, experiment_id, limit=limit)
    return [ExperimentLogRead.model_validate(log) for log in logs]


# 0db51e10 I2(5e): record a ``cross_persona_call`` audit row. The CLI
# facade (``map experiment status --persona-compare``) calls this with
# the per-persona view diff so admin / R6 metrics can aggregate by
# ``result_partition_count`` / ``diff_size`` later.
#
# authz PR2: capability-gated. Whitelist = ``host`` persona OR
# ``role == admin`` via ``system:cross_persona_call`` capability. A
# denied call STILL emits an audit row with ``rejected=True`` + reason
# so R6 metrics can spot probe attempts vs legitimate aggregations.
@experiments_router.post(
    "/experiments/{experiment_id}/cross-persona-call",
    response_model=AuditLogRead,
    status_code=status.HTTP_201_CREATED,
)
def record_cross_persona_call(
    experiment_id: uuid.UUID,
    payload: CrossPersonaCallRecord,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> AuditLogRead:
    perm.ensure_experiment_access(db, agent, experiment_id)
    experiment = svc.get_experiment(db, experiment_id)
    if not agent.has_capability("system:cross_persona_call"):
        # Record the rejected attempt for R6 noise / intrusion detection,
        # then surface the 403 to the caller.
        entry = audit_service.log_cross_persona_call_no_commit(
            db,
            caller_agent_id=agent.id,
            target_experiment_id=experiment_id,
            project_id=experiment.project_id,
            visibility_diff=payload.visibility_diff,
            result_partition_count=payload.result_partition_count,
            diff_size=payload.diff_size,
            rejected=True,
            rejection_reason="missing capability system:cross_persona_call",
        )
        db.commit()
        raise perm.ForbiddenError(
            "Agent lacks capability system:cross_persona_call"
        )
    entry = audit_service.log_cross_persona_call_no_commit(
        db,
        caller_agent_id=agent.id,
        target_experiment_id=experiment_id,
        project_id=experiment.project_id,
        visibility_diff=payload.visibility_diff,
        result_partition_count=payload.result_partition_count,
        diff_size=payload.diff_size,
    )
    db.commit()
    return AuditLogRead.model_validate(entry)


# --- Experiment execution lock (CP-3) -------------------------------------


class ExperimentLockAcquirePayload(BaseModel):
    ttl_seconds: int = Field(default=lock_service.DEFAULT_LOCK_TTL_SECONDS, ge=1)


class ExperimentLockForceReleasePayload(BaseModel):
    reason: str = Field(min_length=1, max_length=512)


class ExperimentLockSkipPayload(BaseModel):
    next_attempt_at: datetime


@experiments_router.post(
    "/experiments/{experiment_id}/lock/acquire",
    response_model=ExperimentLockRead,
)
def acquire_experiment_lock_endpoint(
    experiment_id: uuid.UUID,
    payload: ExperimentLockAcquirePayload,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentLockRead:
    # authz (0e6926fa) PR1: two-gate guard (404 then 403) — must run
    # before any state mutation. Order is load-bearing: a 404 must not
    # be leaked as 403 (or vice versa) for cross-project probes.
    experiment_access.ensure_experiment_creator_or_admin(db, agent, experiment_id)
    result = lock_service.acquire_experiment_lock(
        db,
        experiment_id,
        agent,
        ttl_seconds=payload.ttl_seconds,
    )
    return ExperimentLockRead.model_validate(result)


@experiments_router.post(
    "/experiments/{experiment_id}/lock/release",
    response_model=ExperimentLockRead,
)
def release_experiment_lock_endpoint(
    experiment_id: uuid.UUID,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentLockRead:
    experiment_access.ensure_experiment_creator_or_admin(db, agent, experiment_id)
    result = lock_service.release_experiment_lock(db, experiment_id, agent)
    return ExperimentLockRead.model_validate(result)


@experiments_router.post(
    "/experiments/{experiment_id}/lock/force-release",
    response_model=ExperimentLockRead,
)
def force_release_experiment_lock_endpoint(
    experiment_id: uuid.UUID,
    payload: ExperimentLockForceReleasePayload,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentLockRead:
    experiment_access.ensure_experiment_creator_or_admin(db, agent, experiment_id)
    result = lock_service.force_release_experiment_lock(
        db,
        experiment_id,
        agent,
        reason=payload.reason,
    )
    return ExperimentLockRead.model_validate(result)


@experiments_router.post(
    "/experiments/{experiment_id}/lock/skip",
    response_model=ExperimentLockRead,
)
def record_experiment_lock_skip_endpoint(
    experiment_id: uuid.UUID,
    payload: ExperimentLockSkipPayload,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentLockRead:
    experiment_access.ensure_experiment_creator_or_admin(db, agent, experiment_id)
    result = lock_service.record_experiment_lock_skip(
        db,
        experiment_id,
        agent,
        next_attempt_at=payload.next_attempt_at,
    )
    return ExperimentLockRead.model_validate(result)


@experiments_router.post(
    "/experiments/lock/scan-stalled",
    response_model=ExperimentLockStalledScanRead,
)
def scan_stalled_experiment_locks_endpoint(
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentLockStalledScanRead:
    if not (perm.is_admin(agent) or agent.name == HOST_AGENT_NAME):
        raise ForbiddenError("Only host or admin can scan stalled experiment locks")
    notification_ids = notification_service.notify_stalled_experiment_locks(
        db,
        project_id=None if perm.is_admin(agent) else agent.project_id,
    )
    return ExperimentLockStalledScanRead(
        notification_ids=notification_ids,
        emitted_count=len(notification_ids),
    )
