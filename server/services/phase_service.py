import uuid
from collections import Counter
from typing import Any

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
from server.services.review_service import assert_approve_eligibility


def _sync_phase_owner(experiment) -> None:
    """Mirror ``experiment.phase_owner`` to the resolver's table.

    Called immediately after every ``experiment.phase = ...`` assignment
    in this module so the two never drift. Kept as a thin wrapper so a
    future override path (e.g. admin override, experiment.phase_owner
    column hand-edit) can plug in here without touching every site.

    Type-annotation uses a string forward reference (``Experiment``) so
    this helper can live next to its callers without pulling the heavy
    model imports into this module's top-level namespace.
    """
    experiment.phase_owner = owner_for(experiment.phase).value


def submit_for_review(db: Session, experiment_id: uuid.UUID, actor: Agent) -> None:
    experiment = get_experiment(db, experiment_id)
    if experiment.creator_agent_id != actor.id and actor.role != AgentRole.admin:
        raise ForbiddenError("Only the creator can submit for review")
    if experiment.current_plan_version < 1:
        raise StateTransitionError("Experiment must have a plan before review")
    validate_phase_transition(experiment.phase, ExperimentPhase.review)
    experiment.phase = ExperimentPhase.review
    _sync_phase_owner(experiment)
    db.commit()


def approve_experiment(db: Session, experiment_id: uuid.UUID, actor: Agent) -> None:
    experiment = get_experiment(db, experiment_id)
    if experiment.creator_agent_id != actor.id and actor.role != AgentRole.admin:
        raise ForbiddenError("Only the creator can approve the experiment")
    assert_approve_eligibility(db, experiment)
    validate_phase_transition(experiment.phase, ExperimentPhase.approved)
    experiment.phase = ExperimentPhase.approved
    _sync_phase_owner(experiment)
    db.commit()


def withdraw_from_review(db: Session, experiment_id: uuid.UUID, actor: Agent) -> None:
    experiment = get_experiment(db, experiment_id)
    if experiment.creator_agent_id != actor.id and actor.role != AgentRole.admin:
        raise ForbiddenError("Only the creator can withdraw from review")
    validate_phase_transition(experiment.phase, ExperimentPhase.draft)
    experiment.phase = ExperimentPhase.draft
    _sync_phase_owner(experiment)
    db.commit()


def cancel_experiment(db: Session, experiment_id: uuid.UUID, actor: Agent) -> None:
    experiment = get_experiment(db, experiment_id)
    if experiment.creator_agent_id != actor.id and actor.role != AgentRole.admin:
        raise ForbiddenError("Only the creator can cancel the experiment")
    validate_phase_transition(experiment.phase, ExperimentPhase.cancelled)
    experiment.phase = ExperimentPhase.cancelled
    _sync_phase_owner(experiment)
    db.commit()


def start_experiment(db: Session, experiment_id: uuid.UUID, actor: Agent) -> None:
    experiment = get_experiment(db, experiment_id)
    if experiment.creator_agent_id != actor.id and actor.role != AgentRole.admin:
        raise ForbiddenError("Only the creator can start the experiment")
    validate_phase_transition(experiment.phase, ExperimentPhase.running)
    experiment.phase = ExperimentPhase.running
    _sync_phase_owner(experiment)
    db.commit()


def _ensure_result_reviewer(experiment_creator_id: uuid.UUID, actor: Agent) -> None:
    if actor.role == AgentRole.admin:
        return
    if actor.id == experiment_creator_id:
        raise ForbiddenError("Experiment result must be reviewed by another agent")


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


def _legacy_accept_result_metadata(payload_metadata: dict | None) -> dict[str, Any]:
    """Attach the legacy / pre-schema marker when caller omits verdict_file."""
    fragment = {"pre_schema_accept_result": "true", "verdict_breakdown": None}
    if payload_metadata:
        return {**payload_metadata, **fragment}
    return fragment


def complete_experiment(
    db: Session,
    experiment_id: uuid.UUID,
    actor: Agent,
    payload: ExperimentComplete,
) -> None:
    experiment = get_experiment(db, experiment_id)
    if experiment.creator_agent_id != actor.id and actor.role != AgentRole.admin:
        raise ForbiddenError("Only the creator can complete the experiment")
    validate_phase_transition(experiment.phase, ExperimentPhase.result_review)
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
            content_md=payload.content_md,
            metadata=payload.metadata,
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
    _ensure_result_reviewer(experiment.creator_agent_id, actor)
    validate_phase_transition(experiment.phase, ExperimentPhase.done)

    # === A2 cascade: 同事务把关联的 open action_item 自动 done ===
    open_items = db.scalars(
        select(TopicActionItem).where(
            TopicActionItem.linked_experiment_id == experiment_id,
            TopicActionItem.status == TopicActionItemStatus.open,
        )
    ).all()
    cascaded: list[dict] = []
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
    experiment.phase = ExperimentPhase.done
    _sync_phase_owner(experiment)

    audit_service._log_no_commit(
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
    # I1(d): the host creator of the experiment is structurally forbidden
    # from rejecting their own result. That intent lives on the per-item
    # ``resolve-item --status rebutted`` path during the review phase, so
    # misuse here gets a structured subcode instead of a generic 403.
    if (
        actor.role != AgentRole.admin
        and actor.id == experiment.creator_agent_id
    ):
        raise_reject_result_misuse(
            actor_id=actor.id,
            experiment_id=experiment_id,
            detail=(
                "Experiment creator cannot reject their own result. "
                "单 item 驳回请用 resolve-item --status rebutted, "
                "整个实验驳回需 reviewer / admin。"
            ),
        )
    # Non-creator non-admin callers (reviewer) still pass through the legacy
    # ``_ensure_result_reviewer`` guard. Admin bypass is intentional for
    # back-compat (admins can substitute-reject when a reviewer is absent).
    _ensure_result_reviewer(experiment.creator_agent_id, actor)
    validate_phase_transition(experiment.phase, ExperimentPhase.running)
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
