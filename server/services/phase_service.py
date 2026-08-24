import logging
import uuid
from collections import Counter
from typing import Any, cast

from map_types.enums import ExperimentMode
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import (
    Agent,
    AgentRole,
    ExperimentPhase,
    Review,
    ReviewItem,
    TopicActionItem,
    TopicActionItemStatus,
)
from server.domain.schemas import (
    ExperimentComplete,
    ExperimentLogCreate,
    ExperimentResultDecision,
    ReviewVerdict,
    ReviewVerdictFile,
)
from server.domain.state_machine import validate_phase_transition
from server.services import audit_service, topic_service
from server.services.errors import ForbiddenError, StateTransitionError
from server.services.evidence_service import EVIDENCE_METADATA_KEYS, metadata_has_completion_evidence
from server.services.log_service import append_log
from server.services.phase_owner_resolver import owner_for
from server.services.project_service import get_experiment
from server.services.review_service import (
    assert_approve_eligibility,
    has_review_on_current_plan_version,
    has_review_on_older_plan_version,
)


def _sync_phase_owner(experiment) -> None:
    """Mirror ``experiment.phase_owner`` to the resolver's table.

    Called immediately after every ``experiment.phase = ...`` assignment
    in this module so the two never drift. Kept as a thin wrapper so a
    future override path (e.g. admin override, experiment.phase_owner
    column hand-edit) can plug in here without touching every site.

    In ``direct`` mode (v0.10), the resolver routes ``running`` to
    ``participant`` instead of ``host``.

    Type-annotation uses a string forward reference (``Experiment``) so
    this helper can live next to its callers without pulling the heavy
    model imports into this module's top-level namespace.
    """
    experiment.phase_owner = owner_for(
        experiment.phase, mode=getattr(experiment, "mode", "standard")
    ).value


def _ensure_creator_or_admin(experiment, actor: Agent) -> None:
    """Host-only lifecycle gate: creator OR admin.

    Used by create / submit-review / approve / start / withdraw / cancel.
    The host retains decision authority for these transitions even when
    execution has been delegated to another agent via
    ``executor_agent_id`` (migration 042).
    """
    if experiment.creator_agent_id == actor.id:
        return
    if actor.role == AgentRole.admin:
        return
    raise ForbiddenError("Only the experiment creator or an admin can perform this action")


def _resolve_executor_id(experiment) -> uuid.UUID:
    """Return the agent_id authorized to call ``complete``.

    Falls back to ``creator_agent_id`` for legacy experiments where
    ``executor_agent_id`` is NULL (created before migration 042) so the
    host-self-executes behavior is preserved.
    """
    return cast(uuid.UUID, experiment.executor_agent_id or experiment.creator_agent_id)


def _ensure_can_complete(experiment, actor: Agent) -> None:
    """Executor gate for ``complete_experiment``.

    Only the designated executor (or an admin) may submit the result.
    For legacy experiments (``executor_agent_id IS NULL``) the creator
    remains the executor — preserving the pre-042 host-self-executes
    behavior with zero migration cost.
    """
    if actor.role == AgentRole.admin:
        return
    if actor.id == _resolve_executor_id(experiment):
        return
    raise ForbiddenError(
        "Only the designated executor (or an admin) can complete the experiment"
    )


def submit_for_review(db: Session, experiment_id: uuid.UUID, actor: Agent) -> None:
    experiment = get_experiment(db, experiment_id)
    _ensure_creator_or_admin(experiment, actor)
    if experiment.current_plan_version < 1:
        raise StateTransitionError("Experiment must have a plan before review")
    validate_phase_transition(experiment.phase, ExperimentPhase.review, mode=experiment.mode)
    experiment.phase = ExperimentPhase.review
    _sync_phase_owner(experiment)
    db.commit()


def approve_experiment(db: Session, experiment_id: uuid.UUID, actor: Agent) -> None:
    experiment = get_experiment(db, experiment_id)
    _ensure_creator_or_admin(experiment, actor)
    assert_approve_eligibility(db, experiment)
    validate_phase_transition(experiment.phase, ExperimentPhase.approved, mode=experiment.mode)
    experiment.phase = ExperimentPhase.approved
    _sync_phase_owner(experiment)
    db.commit()


def withdraw_from_review(db: Session, experiment_id: uuid.UUID, actor: Agent) -> None:
    experiment = get_experiment(db, experiment_id)
    _ensure_creator_or_admin(experiment, actor)
    validate_phase_transition(experiment.phase, ExperimentPhase.draft, mode=experiment.mode)
    experiment.phase = ExperimentPhase.draft
    _sync_phase_owner(experiment)
    db.commit()


def cancel_experiment(db: Session, experiment_id: uuid.UUID, actor: Agent) -> None:
    experiment = get_experiment(db, experiment_id)
    _ensure_creator_or_admin(experiment, actor)
    validate_phase_transition(experiment.phase, ExperimentPhase.cancelled, mode=experiment.mode)
    experiment.phase = ExperimentPhase.cancelled
    _sync_phase_owner(experiment)
    db.commit()


def start_experiment(
    db: Session,
    experiment_id: uuid.UUID,
    actor: Agent,
    executor_agent_id: uuid.UUID | None = None,
) -> None:
    """Transition to ``running`` and optionally delegate execution.

    Standard mode: ``approved → running``.
    Direct mode (v0.10): ``draft → running`` (skips review/approved).

    ``actor`` must be the host creator (or admin) — the host retains the
    decision to *start* the experiment. ``executor_agent_id`` designates
    who may call ``complete`` later:

    - ``None`` (default): the host self-executes; ``executor_agent_id``
      is set to ``actor.id`` so the field is always populated on new
      experiments.
    - A different agent's UUID (typically a ``participant`` persona):
      that agent becomes the sole non-admin caller allowed to submit
      the result via ``complete``. The host gives up the ``complete``
      permission but keeps every other lifecycle gate.

    The designated executor must be a member of the same project; an
    out-of-project ``executor_agent_id`` is rejected with 403.
    """
    experiment = get_experiment(db, experiment_id)
    _ensure_creator_or_admin(experiment, actor)
    # Default: host self-executes. Explicit None means "I'll run it
    # myself" — populate the column so downstream code can rely on
    # ``executor_agent_id`` being non-null for new experiments.
    resolved_executor_id = executor_agent_id or actor.id
    # Validate the executor is a member of the same project. Admins
    # bypass this so an admin host can delegate cross-project (rare
    # but supported for ops scenarios).
    if resolved_executor_id != actor.id and actor.role != AgentRole.admin:
        from server.domain.models import Agent as _AgentModel

        executor = db.get(_AgentModel, resolved_executor_id)
        if executor is None:
            raise ForbiddenError(
                f"Executor agent {resolved_executor_id} not found"
            )
        if executor.project_id != experiment.project_id:
            raise ForbiddenError(
                "Executor must be a member of the same project as the experiment"
            )
    validate_phase_transition(experiment.phase, ExperimentPhase.running, mode=experiment.mode)
    experiment.executor_agent_id = resolved_executor_id
    experiment.phase = ExperimentPhase.running
    _sync_phase_owner(experiment)
    db.commit()


def _ensure_result_reviewer(
    experiment_creator_id: uuid.UUID,
    actor: Agent,
    experiment_executor_id: uuid.UUID | None = None,
) -> None:
    """Block the creator AND the executor from reviewing their own result.

    The reviewer-isolation invariant (introduced pre-042) used to block
    only the creator. Migration 042 makes execution delegatable, so the
    executor must also be blocked from accepting their own result —
    otherwise a host could delegate to a participant and then
    double-hat as reviewer to accept it. Admins always bypass.
    """
    if actor.role == AgentRole.admin:
        return
    if actor.id == experiment_creator_id:
        raise ForbiddenError("Experiment result must be reviewed by another agent")
    if experiment_executor_id is not None and actor.id == experiment_executor_id:
        raise ForbiddenError(
            "Experiment result must be reviewed by another agent "
            "(executor cannot self-review)"
        )


# I1(d): the host creator of the experiment is structurally forbidden from
# rejecting their own result — that intent is modelled by the per-item
# ``resolve-item --status rebutted`` flow during the review phase. Misusing
# ``reject-result`` for the wrong intent (e.g. trying to reject a single
# review item) gets a structured subcode instead of a generic 403 so the
# CLI / SDK can route the user to the right command.
REJECT_RESULT_MISUSE_HINT = (
    "单 item 驳回请用 resolve-item --status rebutted (review 阶段); "
    "整个实验驳回请让 reviewer / admin 调用 reject-result, "
    "host creator 不可拒绝自己的 result。"
)


def raise_reject_result_misuse(
    *,
    actor_id: uuid.UUID,
    experiment_id: uuid.UUID,
    detail: str | None = None,
) -> None:
    """Emit a :class:`StateTransitionError` carrying the
    ``REVIEW_REJECT_RESULT_MISUSE`` subcode.

    Used when the host creator attempts to call ``reject-result`` (the wrong
    command for their intent) or any caller tries to use ``reject-result``
    as a stand-in for the per-item ``resolve-item --status rebutted`` flow.
    """
    message = detail or "reject-result 被错误使用: " + REJECT_RESULT_MISUSE_HINT
    raise StateTransitionError(
        message,
        error_code="REVIEW_REJECT_RESULT_MISUSE",
        hint=REJECT_RESULT_MISUSE_HINT,
        retryable=False,
    )


def _validate_and_summarize_verdict_file(
    db: Session, experiment_id: uuid.UUID, verdict_file: ReviewVerdictFile
) -> dict[str, Any]:
    """Validate item_id.review_id ownership and compute R6 verdict breakdown.

    The plan (b): ownership is keyed on review_id. Two crossing cases rejected:
      - same reviewer across experiments (item_id from a closed experiment)
      - different reviewer same experiment (item_id from another reviewer's
        review on the same experiment)

    Strict interpretation: each item_id's actual ``review_id`` must equal
    ``verdict_file.review_id`` AND that review must belong to the current
    experiment. The verdict_file's review_id must likewise belong to this
    experiment. Anything else → 403.

    Returns a metadata fragment to merge into ``experiment_logs.metadata_json``
    with ``pre_schema_accept_result='false'`` + the verdict breakdown. Caller
    is responsible for adding it to the log.
    """
    experiment_review_ids = set(
        db.scalars(select(Review.id).where(Review.experiment_id == experiment_id)).all()
    )
    if verdict_file.review_id not in experiment_review_ids:
        raise ForbiddenError(
            f"verdict_file.review_id {verdict_file.review_id} does not belong "
            f"to experiment {experiment_id}"
        )
    verdict_review_item_ids = set(
        db.scalars(
            select(ReviewItem.id).where(ReviewItem.review_id == verdict_file.review_id)
        ).all()
    )
    for verdict in verdict_file.verdicts:
        if verdict.item_id not in verdict_review_item_ids:
            raise ForbiddenError(
                f"verdict item_id {verdict.item_id} does not belong to "
                f"verdict_file.review_id {verdict_file.review_id} on experiment "
                f"{experiment_id}"
            )
    for invariant in verdict_file.invariants:
        if invariant.item_id not in verdict_review_item_ids:
            raise ForbiddenError(
                f"invariant item_id {invariant.item_id} does not belong to "
                f"verdict_file.review_id {verdict_file.review_id} on experiment "
                f"{experiment_id}"
            )
    breakdown = Counter(v.verdict.value for v in verdict_file.verdicts)
    return {
        "pre_schema_accept_result": "false",
        "verdict_breakdown": {
            "passed": breakdown.get(ReviewVerdict.passed.value, 0),
            "failed": breakdown.get(ReviewVerdict.failed.value, 0),
            "waived": breakdown.get(ReviewVerdict.waived.value, 0),
        },
        "verdict_file": verdict_file.model_dump(mode="json"),
    }


def _legacy_accept_result_metadata(payload_metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Attach the legacy / pre-schema marker when caller omits verdict_file."""
    fragment = {"pre_schema_accept_result": "true", "verdict_breakdown": None}
    if payload_metadata:
        return {**payload_metadata, **fragment}
    return fragment


# --- 50cddb7e I4 (A3/A4): pytest_summary 机器校验的门禁接线 --------------------


def _validation_metadata(validation) -> dict[str, Any]:
    """Persistable form of ``PytestSummaryValidation`` (log metadata_json)."""
    return {
        "status": "exempted" if validation.exempted else "passed",
        "known_failures": (
            list(validation.exempted_known_failures)
            if validation.exempted
            else []
        ),
        "warnings": list(validation.warnings),
    }


def _format_pytest_summary_validation(validation) -> str:
    """Human-readable lines appended to the completion log (reviewer-visible)."""
    lines = ["", "## pytest_summary 机器校验（50cddb7e I4 A3）"]
    lines.append(f"- status: {'exempted' if validation.exempted else 'passed'}")
    if validation.exempted:
        refs = " ".join(validation.exempted_known_failures)
        lines.append(f"- known_failures 豁免: {refs}")
    for warn in validation.warnings:
        lines.append(f"- [WARN] {warn}")
    return "\n".join(lines)


def _apply_pytest_summary_gate(
    payload: ExperimentComplete,
) -> tuple[Any, dict[str, Any] | None] | None:
    """Run the complete-time pytest_summary machine check.

    Returns ``(validation, enriched_metadata)`` when ``metadata`` carries a
    ``pytest_summary``; raises ``StateTransitionError`` on ``failed > 0``
    without ``known_failures`` exemption. Returns None when there is no
    ``pytest_summary`` to machine-check.
    """
    if not isinstance(payload.metadata, dict):
        return None
    if payload.metadata.get("pytest_summary") is None:
        return None
    from server.config import get_settings  # lazy: avoid import cycle
    from server.services.evidence_service import validate_pytest_summary

    validation = validate_pytest_summary(
        payload.metadata["pytest_summary"],
        known_failures=payload.known_failures,
        ci_baseline_total=get_settings().ci_test_total,
    )
    if validation.reject_reason:
        raise StateTransitionError(validation.reject_reason)
    # 只要门禁跑过就落 marker（绿灯 passed 也可见，A4 reviewer 视角统一）。
    enriched = {
        **(payload.metadata or {}),
        "pytest_summary_validation": _validation_metadata(validation),
    }
    return (validation, enriched)


def _recheck_pytest_summary_for_accept(
    db: Session, experiment_id: uuid.UUID
) -> tuple[str, dict[str, Any]] | None:
    """Re-run the pytest_summary machine check at accept time (A4).

    Reads the experiment's most recent log that carries a structured
    ``pytest_summary`` in ``metadata_json``, re-validates it, and returns
    ``(reviewer-visible verdict line, pytest_summary_validation metadata)``.
    Returns None when no such evidence exists (nothing to recheck).
    """
    from server.domain.models import ExperimentLog

    rows = db.scalars(
        select(ExperimentLog)
        .where(ExperimentLog.experiment_id == experiment_id)
        .order_by(ExperimentLog.created_at.desc(), ExperimentLog.id.desc())
        .limit(20)
    ).all()
    summary: Any = None
    for row in rows:
        meta = row.metadata_json or {}
        if isinstance(meta, dict) and meta.get("pytest_summary") is not None:
            summary = meta["pytest_summary"]
            break
    if summary is None:
        return None

    from server.config import get_settings  # lazy: avoid import cycle
    from server.services.evidence_service import validate_pytest_summary

    validation = validate_pytest_summary(
        summary,
        known_failures=[],  # 豁免决策已在 complete 时记录；accept 复显当前灯
        ci_baseline_total=get_settings().ci_test_total,
    )
    status = "exempted" if validation.exempted else "passed"
    lines = [
        "## pytest_summary 机器校验（50cddb7e I4 A4 · accept 复校）",
        f"- status: {status}",
    ]
    for warn in validation.warnings:
        lines.append(f"- [WARN] {warn}")
    return (
        "\n".join(lines),
        {"pytest_summary_validation": {"status": status, "warnings": list(validation.warnings)}},
    )


def complete_experiment(
    db: Session,
    experiment_id: uuid.UUID,
    actor: Agent,
    payload: ExperimentComplete,
) -> None:
    experiment = get_experiment(db, experiment_id)
    _ensure_can_complete(experiment, actor)

    # 实验 bd9b21f6 (plan-revision-review-gate) A2: breaking 打回期间 complete
    # 一律拒——报错必须 actionable（提示等待重评 plan 版本与剩余阻塞数，
    # 而非通用 invalid transition）。
    if experiment.phase == ExperimentPhase.pending_review:
        from server.services.review_service import count_open_unreasonable_for_experiment

        open_count = count_open_unreasonable_for_experiment(db, experiment_id)
        raise StateTransitionError(
            f"breaking revise 待重评: plan v{experiment.current_plan_version} 标记 "
            f"--breaking-audit 后需 reviewer 重评通过才可 complete; "
            f"open unreasonable items: {open_count}"
        )

    if payload.log_file_path:
        experiment.log_file_path = payload.log_file_path

    log_content = payload.content_md or f"See file: {payload.log_file_path}"

    # 50cddb7e I4 (A3): pytest_summary 机器校验——failed>0 且无 --known-failures
    # 豁免 → 拒绝 complete（硬门禁）；豁免/警告追加进 completion log + metadata，
    # reviewer 在 result_review 侧可直接可见（A4 complete 路径展示）。
    enriched_metadata = payload.metadata
    gate = _apply_pytest_summary_gate(payload)
    if gate is not None:
        validation, enriched_metadata = gate
        log_content += _format_pytest_summary_validation(validation)

    # 实验 bd9b21f6 A5: complete 版本核对红旗（兜底，非阻塞）。当前 plan 版本
    # 无评审覆盖、但存在更旧版本评审 ⇒ 评审通过后 plan 又改过而未回 review——
    # 对疑似「架构级修订漏标 breaking」的 last-line 防线。A3 合法非 breaking
    # 修订同样满足该形态，故红旗只提示核对不挡路（A3 不打断执行流）；评审覆盖
    # 当前版本（A7 解除链路）则无红旗。
    if (
        not has_review_on_current_plan_version(db, experiment)
        and has_review_on_older_plan_version(db, experiment)
    ):
        log_content += (
            "\n\n## ⚠ breaking 漏标红旗（实验 bd9b21f6 A5 兜底）\n"
            f"complete 时当前 plan v{experiment.current_plan_version} 无评审覆盖"
            f"（最后评审见更旧 plan 版本）。若非 breaking 修订可忽略本红旗；"
            "若实为架构级修订却未标 --breaking-audit，请 reviewer 在 result_review"
            " 指出——本应回 pending_review 重评后再 complete。"
        )

    is_direct = experiment.mode == ExperimentMode.direct.value

    if is_direct:
        # v0.10 direct mode: running → done (skip result_review).
        # Evidence metadata is a soft warning, not a hard gate.
        validate_phase_transition(
            experiment.phase, ExperimentPhase.done, mode=experiment.mode
        )
        if not metadata_has_completion_evidence(payload.metadata):
            logging.warning(
                "direct-mode experiment %s completed without evidence metadata",
                experiment_id,
            )
        append_log(
            db,
            experiment_id,
            actor,
            ExperimentLogCreate(
                summary=payload.summary,
                content_md=log_content,
                metadata=enriched_metadata,
            ),
        )
        # A2 cascade: mark linked open action_items as done (same as
        # accept_result in standard mode).
        open_items = db.scalars(
            select(TopicActionItem).where(
                TopicActionItem.linked_experiment_id == experiment_id,
                TopicActionItem.status == TopicActionItemStatus.open,
            )
        ).all()
        cascaded: list[dict[str, Any]] = []
        for item in open_items:
            cascaded.append(
                topic_service._complete_action_item_no_commit(
                    db,
                    item,
                    triggered_by=f"experiment.completed:{experiment_id}",
                )
            )
        experiment.phase = ExperimentPhase.done
        _sync_phase_owner(experiment)
        audit_service.log_no_commit(
            db,
            action="experiment.completed",
            target_type="experiment",
            target_id=experiment_id,
            agent_id=actor.id,
            project_id=experiment.project_id,
            summary=f"实验完成「{experiment.title}」(direct)",
            payload={
                "experiment_id": str(experiment_id),
                "mode": "direct",
                "cascaded_action_items": cascaded,
            },
        )
        db.commit()
        return

    # --- standard mode (unchanged) ---
    validate_phase_transition(
        experiment.phase, ExperimentPhase.result_review, mode=experiment.mode
    )
    if not metadata_has_completion_evidence(payload.metadata):
        keys = ", ".join(sorted(EVIDENCE_METADATA_KEYS))
        raise StateTransitionError(
            "Experiment complete requires deployment/test evidence metadata "
            f"(accepted keys include: {keys}; or evidence/allow_missing_evidence)."
        )
    append_log(
        db,
        experiment_id,
        actor,
        ExperimentLogCreate(
            summary=payload.summary,
            content_md=log_content,
            metadata=enriched_metadata,
        ),
    )
    experiment.phase = ExperimentPhase.result_review
    _sync_phase_owner(experiment)
    db.commit()


def accept_result(
    db: Session,
    experiment_id: uuid.UUID,
    actor: Agent,
    payload: ExperimentResultDecision,
) -> None:
    experiment = get_experiment(db, experiment_id)
    _ensure_result_reviewer(
        experiment.creator_agent_id, actor, experiment.executor_agent_id
    )
    validate_phase_transition(experiment.phase, ExperimentPhase.done, mode=experiment.mode)

    # === A2 cascade: 同事务把关联的 open action_item 自动 done ===
    open_items = db.scalars(
        select(TopicActionItem).where(
            TopicActionItem.linked_experiment_id == experiment_id,
            TopicActionItem.status == TopicActionItemStatus.open,
        )
    ).all()
    cascaded: list[dict[str, Any]] = []
    for item in open_items:
        cascaded.append(
            topic_service._complete_action_item_no_commit(
                db,
                item,
                triggered_by=f"experiment.completed:{experiment_id}",
            )
        )

    if payload.verdict_file is not None:
        verdict_fragment = _validate_and_summarize_verdict_file(
            db, experiment_id, payload.verdict_file
        )
        merged_metadata = (
            {**(payload.metadata or {}), **verdict_fragment}
            if payload.metadata
            else verdict_fragment
        )
    else:
        merged_metadata = _legacy_accept_result_metadata(payload.metadata)

    # 50cddb7e I4 (A4): accept 路径复显 pytest_summary 机器校验——从实验最近
    # 一条携带 pytest_summary 的 log 取回 evidence，重跑校验并把当前红/绿灯
    # 追加进 accept log（content + metadata），reviewer 结果处直接可见。
    accept_recheck = _recheck_pytest_summary_for_accept(db, experiment_id)
    if accept_recheck:
        verdict_line, recheck_metadata = accept_recheck
        merged_metadata = {**merged_metadata, **recheck_metadata}
        content_md = (payload.content_md or "") + "\n\n" + verdict_line
    else:
        content_md = payload.content_md

    append_log(
        db,
        experiment_id,
        actor,
        ExperimentLogCreate(
            summary=payload.summary,
            content_md=content_md,
            metadata=merged_metadata,
        ),
    )
    experiment.phase = ExperimentPhase.done
    _sync_phase_owner(experiment)

    audit_service.log_no_commit(
        db,
        action="experiment.completed",
        target_type="experiment",
        target_id=experiment_id,
        agent_id=actor.id,
        project_id=experiment.project_id,
        summary=f"实验完成「{experiment.title}」",
        payload={
            "experiment_id": str(experiment_id),
            "cascaded_action_items": cascaded,
            "pre_schema_accept_result": "true"
            if payload.verdict_file is None
            else "false",
        },
    )

    db.commit()


def reject_result(
    db: Session,
    experiment_id: uuid.UUID,
    actor: Agent,
    payload: ExperimentResultDecision,
) -> None:
    experiment = get_experiment(db, experiment_id)
    # I1(d): the host creator (and now, post-042, the designated executor)
    # of the experiment is structurally forbidden from rejecting their own
    # result. That intent lives on the per-item ``resolve-item --status
    # rebutted`` path during the review phase, so misuse here gets a
    # structured subcode instead of a generic 403.
    if (
        actor.role != AgentRole.admin
        and (
            actor.id == experiment.creator_agent_id
            or (
                experiment.executor_agent_id is not None
                and actor.id == experiment.executor_agent_id
            )
        )
    ):
        raise_reject_result_misuse(
            actor_id=actor.id,
            experiment_id=experiment_id,
            detail=(
                "Experiment creator / executor cannot reject their own result. "
                "单 item 驳回请用 resolve-item --status rebutted, "
                "整个实验驳回需 reviewer / admin。"
            ),
        )
    # Non-creator non-executor non-admin callers (reviewer) still pass
    # through the legacy ``_ensure_result_reviewer`` guard. Admin bypass
    # is intentional for back-compat (admins can substitute-reject when
    # a reviewer is absent).
    _ensure_result_reviewer(
        experiment.creator_agent_id, actor, experiment.executor_agent_id
    )
    validate_phase_transition(experiment.phase, ExperimentPhase.running, mode=experiment.mode)
    if payload.verdict_file is not None:
        verdict_fragment = _validate_and_summarize_verdict_file(
            db, experiment_id, payload.verdict_file
        )
        merged_metadata = (
            {**(payload.metadata or {}), **verdict_fragment}
            if payload.metadata
            else verdict_fragment
        )
    else:
        merged_metadata = _legacy_accept_result_metadata(payload.metadata)
    append_log(
        db,
        experiment_id,
        actor,
        ExperimentLogCreate(
            summary=payload.summary,
            content_md=payload.content_md,
            metadata=merged_metadata,
        ),
    )
    # I1(e): experiment-level reject-result also writes a
    # ``review_item.mutation`` audit row at the experiment scope so admins
    # can filter the audit log by ``--experiment <id>`` and see the
    # reject-result alongside per-item mutations.
    audit_service.log_review_item_mutation_no_commit(
        db,
        item=None,
        experiment_id=experiment_id,
        actor_id=actor.id,
        project_id=experiment.project_id,
        action="reject_result",
        before_state="result_review",
        after_state="running",
        reason=payload.summary,
        target_id=experiment_id,
    )
    experiment.phase = ExperimentPhase.running
    _sync_phase_owner(experiment)
    db.commit()
