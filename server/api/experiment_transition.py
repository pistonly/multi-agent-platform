"""实验 lifecycle transition 协议端点（实验B 24f3e565 / B1~B8）。

三个协议端点 + 单体端点共用的执行/扇出 helper：

- ``POST /experiments/{id}/transition/validate``：签发七元组 token（B1）。
- ``POST /experiments/{id}/transition/commit``：CAS 提交 + receipt（B2/B5）。
- ``GET  /experiments/{id}/transition/receipts``：回执列表（recover 冲突
  报告的「胜出 receipt」证据源，B4）。
- ``GET  /experiments/{id}/transition/receipts/{nonce}``：按 nonce 查回执
  （recover 的「token 是否已提交」判定源，404 = 未提交/未知，B3）。

``emit_transition_effects`` 把原单体端点的 audit/notification 载荷逐字
移植——两跳路径与单体包装路径由此产生同构的 audit/receipt/通知（B7），
且仅在 ``replayed=False`` 时执行一次（B2 重放幂等）。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from map_types.schemas.experiment import (
    ExperimentComplete,
    ExperimentResultDecision,
    ExperimentStart,
    ExperimentTransitionCommitRequest,
    ExperimentTransitionCommitResponse,
    ExperimentTransitionReceipt,
    ExperimentTransitionValidateRequest,
    ExperimentTransitionVerdict,
    TemplateValidationSchema,
    TemplateWarningSchema,
)
from sqlalchemy.orm import Session

from server.api.background_tasks import bind_background_tasks
from server.api.common import emit
from server.api.deps import get_current_agent
from server.db.session import get_db
from server.domain.models import Agent, ExperimentPhase
from server.services import notification_service
from server.services import permissions as perm
from server.services import project_service as svc
from server.services.errors import NotFoundError
from server.services.experiment_transition_service import (
    commit_transition,
    get_receipt,
    list_receipts,
    run_transition,
    validate_transition,
)
from server.services.template_service import validate_result_submission_template

transition_router = APIRouter(
    tags=["experiments"], dependencies=[Depends(bind_background_tasks)]
)


# ---------------------------------------------------------------------------
# 协议端点（两跳路径）
# ---------------------------------------------------------------------------


@transition_router.post(
    "/experiments/{experiment_id}/transition/validate",
    response_model=ExperimentTransitionVerdict,
)
def validate_experiment_transition(
    experiment_id: uuid.UUID,
    payload: ExperimentTransitionValidateRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentTransitionVerdict:
    perm.ensure_experiment_access(db, agent, experiment_id)
    verdict, _token = validate_transition(
        db,
        experiment_id=experiment_id,
        actor=agent,
        action=payload.action,
        to_phase=payload.to_phase,
        base_revision=payload.base_revision,
        workspace_fingerprint=payload.workspace_fingerprint,
        expires_in_seconds=payload.expires_in_seconds,
    )
    return verdict


@transition_router.post(
    "/experiments/{experiment_id}/transition/commit",
    response_model=ExperimentTransitionCommitResponse,
)
def commit_experiment_transition(
    experiment_id: uuid.UUID,
    payload: ExperimentTransitionCommitRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentTransitionCommitResponse:
    perm.ensure_experiment_access(db, agent, experiment_id)
    receipt, snapshot, replayed = commit_transition(
        db,
        experiment_id=experiment_id,
        actor=agent,
        token=payload.token,
        start=payload.start,
        complete=payload.complete,
        decision=payload.decision,
    )
    if not replayed:
        experiment = svc.get_experiment(db, experiment_id)
        emit_transition_effects(db, agent=agent, experiment=experiment, action=receipt.action)
    # complete 专属响应侧装饰：4-段 template soft validation，与单体
    # complete 端点（ExperimentSummaryRead.template_validation）同构。
    # 重放也回带（B2：重放响应逐字段等于首次响应）。
    template_validation = None
    if payload.complete is not None:
        template_result = validate_result_submission_template(payload.complete.content_md)
        template_validation = TemplateValidationSchema(
            warnings=[
                TemplateWarningSchema(code=w.code, section=w.section, detail=w.detail)
                for w in template_result.warnings
            ],
            sections_present=list(template_result.sections_present),
            log_link_count=template_result.log_link_count,
            valid=template_result.valid,
        )
    return _commit_response(receipt, snapshot, replayed, template_validation)


# ---------------------------------------------------------------------------
# receipt 查询（recover 的证据源，B3/B4）
# ---------------------------------------------------------------------------


@transition_router.get(
    "/experiments/{experiment_id}/transition/receipts",
    response_model=list[ExperimentTransitionReceipt],
)
def list_experiment_transition_receipts(
    experiment_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> list[ExperimentTransitionReceipt]:
    perm.ensure_experiment_access(db, agent, experiment_id)
    return list_receipts(db, experiment_id, limit=limit)


@transition_router.get(
    "/experiments/{experiment_id}/transition/receipts/{nonce}",
    response_model=ExperimentTransitionReceipt,
)
def get_experiment_transition_receipt(
    experiment_id: uuid.UUID,
    nonce: str,
    db: Session = Depends(get_db),
    agent: Agent = Depends(get_current_agent),
) -> ExperimentTransitionReceipt:
    perm.ensure_experiment_access(db, agent, experiment_id)
    receipt = get_receipt(db, experiment_id, nonce)
    if receipt is None:
        raise NotFoundError(f"transition receipt not found: {nonce}（未提交或不存在）")
    return receipt


# ---------------------------------------------------------------------------
# 单体端点包装路径共用 helper
# ---------------------------------------------------------------------------


def execute_transition(
    db: Session,
    agent: Agent,
    experiment_id: uuid.UUID,
    *,
    action: str,
    start: ExperimentStart | None = None,
    complete: ExperimentComplete | None = None,
    decision: ExperimentResultDecision | None = None,
) -> tuple[ExperimentTransitionReceipt, dict, bool]:
    """单体端点包装：run_transition（validate+commit 同请求）+ 事件扇出。

    指纹为 server-authoritative 模式（Web 兼容，B10）；audit/notification
    载荷与两跳路径逐字一致（B7 同构）。
    """
    receipt, snapshot, replayed = run_transition(
        db,
        experiment_id=experiment_id,
        actor=agent,
        action=action,
        start=start,
        complete=complete,
        decision=decision,
    )
    if not replayed:
        experiment = svc.get_experiment(db, experiment_id)
        emit_transition_effects(db, agent=agent, experiment=experiment, action=action)
    return receipt, snapshot, replayed


def emit_transition_effects(
    db: Session,
    *,
    agent: Agent,
    experiment,
    action: str,
) -> None:
    """按 action 扇出 audit/notification——载荷自原单体端点逐字移植。"""
    project_id = experiment.project_id
    title = experiment.title
    phase_value = experiment.phase.value
    if action == "submit-review":
        emit(
            db, agent,
            action="experiment.phase_changed",
            target_type="experiment",
            target_id=experiment.id,
            project_id=project_id,
            summary=f"提交评审（{title}）",
            event="experiment.phase_changed",
            event_payload={"id": str(experiment.id), "phase": phase_value, "title": title},
        )
    elif action == "approve":
        emit(
            db, agent,
            action="experiment.phase_changed",
            target_type="experiment",
            target_id=experiment.id,
            project_id=project_id,
            summary=f"批准实验（{title}）",
            event="experiment.phase_changed",
            event_payload={"id": str(experiment.id), "phase": phase_value, "title": title},
        )
    elif action == "withdraw":
        # Phase 2 D2: kind-directed SSE so the waker can map to ``experiment_lifecycle``.
        notification_service.emit_kind(
            db,
            project_id=project_id,
            actor_id=agent.id,
            personas=["host", "reviewer"],
            event="experiment.lifecycle.withdrawn",
            summary=f"实验已撤回评审（{title}）",
            target_type="experiment",
            target_id=experiment.id,
            payload={"experiment_id": str(experiment.id), "title": title, "phase": phase_value},
        )
    elif action == "cancel":
        notification_service.emit_kind(
            db,
            project_id=project_id,
            actor_id=agent.id,
            personas=["host", "reviewer"],
            event="experiment.lifecycle.cancelled",
            summary=f"实验已取消（{title}）",
            target_type="experiment",
            target_id=experiment.id,
            payload={"experiment_id": str(experiment.id), "title": title, "phase": phase_value},
        )
    elif action == "start":
        emit(
            db, agent,
            action="experiment.phase_changed",
            target_type="experiment",
            target_id=experiment.id,
            project_id=project_id,
            summary=f"开始执行（{title}）",
            event="experiment.phase_changed",
            event_payload={
                "id": str(experiment.id),
                "phase": phase_value,
                "title": title,
                # Migration 042: surface executor delegation in the SSE event
                # so waker / web UI can show "host delegated to {executor}".
                "executor_agent_id": (
                    str(experiment.executor_agent_id) if experiment.executor_agent_id else None
                ),
            },
        )
    elif action == "complete":
        # plan-mode-direct-execution-productization 复核 SSE/audit 文案按完成后的
        # phase 区分：direct 完成即 done → "已完成"；standard 完成进 result_review
        # → "待审批"。reviewer / waker / Web UI 看到一致语义。
        is_done = experiment.phase == ExperimentPhase.done
        event_summary = (
            f"实验已完成（{title}）"
            if is_done
            else f"提交实验结果待审批（{title}）"
        )
        emit(
            db, agent,
            action="experiment.phase_changed",
            target_type="experiment",
            target_id=experiment.id,
            project_id=project_id,
            summary=event_summary,
            event="experiment.phase_changed",
            event_payload={
                "id": str(experiment.id),
                "phase": phase_value,
                "title": title,
                # plan-mode-direct-execution-productization 复核：在 SSE payload 显式
                # 标注终态语义，waker/UI 不需要再回头查 mode 字段。
                "completion_state": "done" if is_done else "pending_review",
            },
        )
    elif action == "accept-result":
        emit(
            db, agent,
            action="experiment.phase_changed",
            target_type="experiment",
            target_id=experiment.id,
            project_id=project_id,
            summary=f"审批通过实验结果（{title}）",
            event="experiment.phase_changed",
            event_payload={"id": str(experiment.id), "phase": phase_value, "title": title},
        )
    elif action == "reject-result":
        emit(
            db, agent,
            action="experiment.phase_changed",
            target_type="experiment",
            target_id=experiment.id,
            project_id=project_id,
            summary=f"驳回实验结果（{title}）",
            event="experiment.phase_changed",
            event_payload={"id": str(experiment.id), "phase": phase_value, "title": title},
        )


def _commit_response(
    receipt,
    snapshot: dict,
    replayed: bool,
    template_validation: TemplateValidationSchema | None = None,
) -> ExperimentTransitionCommitResponse:
    return ExperimentTransitionCommitResponse(
        accepted=True,
        replayed=replayed,
        receipt=ExperimentTransitionReceipt.model_validate(receipt),
        experiment_id=snapshot["experiment_id"],
        phase=snapshot["phase"],
        title=snapshot["title"],
        mode=snapshot.get("mode"),
        executor_agent_id=snapshot.get("executor_agent_id"),
        template_validation=template_validation,
    )


# 聚合挂载：与 execution/reviews 同级（experiments.py include）。
