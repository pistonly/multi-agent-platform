"""实验执行域路由（T17 从 experiments.py 拆出）。

职责边界：M3 执行生命周期（start / complete / accept-result /
reject-result）、实验日志（create / list）、cross-persona-call 审计
与 CP-3 执行锁端点（acquire / release / force-release / skip /
scan-stalled）。CRUD 与相位流转（submit / approve / withdraw /
cancel）留在 ``experiments.py``；plans / reviews / comments 在
``experiment_reviews.py``。

三个模块各自持有独立 ``APIRouter``（同 tags、同 bind_background_tasks
依赖），由 ``experiments_router.include_router`` 聚合挂载——URL 契约
不变。
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from server.api.background_tasks import bind_background_tasks
from server.api.common import emit
from server.api.deps import get_current_agent
from server.auth import experiment_access
from server.db.session import get_db
from server.domain.models import Agent
from server.domain.schemas import (
    AuditLogRead,
    CrossPersonaCallRecord,
    EvidenceValidationSchema,
    EvidenceWarningSchema,
    ExperimentComplete,
    ExperimentLockRead,
    ExperimentLockStalledScanRead,
    ExperimentLogCreate,
    ExperimentLogRead,
    ExperimentResultDecision,
    ExperimentStart,
    ExperimentSummaryRead,
    LogCreateResponse,
    SimilarityWarningSchema,
    TemplateValidationSchema,
    TemplateWarningSchema,
)
from server.services import (
    audit_service,
    lock_service,
    log_service,
    notification_service,
    phase_service,
)
from server.services import permissions as perm
from server.services import project_service as svc
from server.services.errors import ForbiddenError
from server.services.experiment_capabilities_service import experiment_summary_for_actor
from server.services.template_service import validate_result_submission_template


def _summary_for_agent(db: Session, experiment, agent: Agent, **extra) -> ExperimentSummaryRead:
    return experiment_summary_for_actor(db, experiment, agent, extra_updates=extra or None)


execution_router = APIRouter(tags=["experiments"], dependencies=[Depends(bind_background_tasks)])


@execution_router.post("/experiments/{experiment_id}/start", response_model=ExperimentSummaryRead)
def start_experiment(
    experiment_id: uuid.UUID,
    payload: ExperimentStart | None = None,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentSummaryRead:
    perm.ensure_experiment_access(db, agent, experiment_id)
    executor_agent_id = payload.executor_agent_id if payload is not None else None
    phase_service.start_experiment(db, experiment_id, agent, executor_agent_id)
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
        event_payload={
            "id": str(experiment_id),
            "phase": experiment.phase.value,
            "title": experiment.title,
            # Migration 042: surface executor delegation in the SSE event
            # so waker / web UI can show "host delegated to {executor}".
            "executor_agent_id": (
                str(experiment.executor_agent_id) if experiment.executor_agent_id else None
            ),
        },
    )
    return _summary_for_agent(db, experiment, agent)


@execution_router.post("/experiments/{experiment_id}/complete", response_model=ExperimentSummaryRead)
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


@execution_router.post("/experiments/{experiment_id}/accept-result", response_model=ExperimentSummaryRead)
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


@execution_router.post("/experiments/{experiment_id}/reject-result", response_model=ExperimentSummaryRead)
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


@execution_router.post(
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
    log, validation, similarity, force_skip_applied, summary_repeat_hint = (
        log_service.create_log(db, experiment_id, agent, payload)
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
        # v0.13 M57 slim form: explicit skip marker + anti-abuse hint
        # (None for the full content_md form).
        similarity_skipped=similarity.skipped_reason,
        summary_repeat_hint=summary_repeat_hint,
    )


@execution_router.get("/experiments/{experiment_id}/logs", response_model=list[ExperimentLogRead])
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
@execution_router.post(
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


@execution_router.post(
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


@execution_router.post(
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


@execution_router.post(
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


@execution_router.post(
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


@execution_router.post(
    "/experiments/lock/scan-stalled",
    response_model=ExperimentLockStalledScanRead,
)
def scan_stalled_experiment_locks_endpoint(
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentLockStalledScanRead:
    # authz PR3: replace the old ``agent.name == HOST_AGENT_NAME``
    # check with a capability-based one. The name check was brittle —
    # it required the host agent to keep a hard-coded name; renaming
    # it (or registering a second host agent) silently disabled the
    # stalled-lock scan. Use the same capability model as the rest of
    # the system: any agent holding ``system:scan_stalled`` (or an
    # admin) can run this.
    if not (
        perm.is_admin(agent)
        or agent.has_capability("system:scan_stalled")
    ):
        raise ForbiddenError(
            "Agent lacks capability system:scan_stalled"
        )
    notification_ids = notification_service.notify_stalled_experiment_locks(
        db,
        project_id=None if perm.is_admin(agent) else agent.project_id,
    )
    return ExperimentLockStalledScanRead(
        notification_ids=notification_ids,
        emitted_count=len(notification_ids),
    )
