import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Agent, AgentRole, ExperimentPhase, TopicActionItem, TopicActionItemStatus
from server.domain.schemas import ExperimentComplete, ExperimentLogCreate, ExperimentResultDecision
from server.domain.state_machine import validate_phase_transition
from server.services import audit_service, topic_service
from server.services.errors import ForbiddenError, StateTransitionError
from server.services.log_service import append_log
from server.services.project_service import get_experiment
from server.services.review_service import assert_approve_eligibility


def submit_for_review(db: Session, experiment_id: uuid.UUID, actor: Agent) -> None:
    experiment = get_experiment(db, experiment_id)
    if experiment.creator_agent_id != actor.id and actor.role != AgentRole.admin:
        raise ForbiddenError("Only the creator can submit for review")
    if experiment.current_plan_version < 1:
        raise StateTransitionError("Experiment must have a plan before review")
    validate_phase_transition(experiment.phase, ExperimentPhase.review)
    experiment.phase = ExperimentPhase.review
    db.commit()


def approve_experiment(db: Session, experiment_id: uuid.UUID, actor: Agent) -> None:
    experiment = get_experiment(db, experiment_id)
    if experiment.creator_agent_id != actor.id and actor.role != AgentRole.admin:
        raise ForbiddenError("Only the creator can approve the experiment")
    assert_approve_eligibility(db, experiment)
    validate_phase_transition(experiment.phase, ExperimentPhase.approved)
    experiment.phase = ExperimentPhase.approved
    db.commit()


def withdraw_from_review(db: Session, experiment_id: uuid.UUID, actor: Agent) -> None:
    experiment = get_experiment(db, experiment_id)
    if experiment.creator_agent_id != actor.id and actor.role != AgentRole.admin:
        raise ForbiddenError("Only the creator can withdraw from review")
    validate_phase_transition(experiment.phase, ExperimentPhase.draft)
    experiment.phase = ExperimentPhase.draft
    db.commit()


def cancel_experiment(db: Session, experiment_id: uuid.UUID, actor: Agent) -> None:
    experiment = get_experiment(db, experiment_id)
    if experiment.creator_agent_id != actor.id and actor.role != AgentRole.admin:
        raise ForbiddenError("Only the creator can cancel the experiment")
    validate_phase_transition(experiment.phase, ExperimentPhase.cancelled)
    experiment.phase = ExperimentPhase.cancelled
    db.commit()


def start_experiment(db: Session, experiment_id: uuid.UUID, actor: Agent) -> None:
    experiment = get_experiment(db, experiment_id)
    if experiment.creator_agent_id != actor.id and actor.role != AgentRole.admin:
        raise ForbiddenError("Only the creator can start the experiment")
    validate_phase_transition(experiment.phase, ExperimentPhase.running)
    experiment.phase = ExperimentPhase.running
    db.commit()


def _ensure_result_reviewer(experiment_creator_id: uuid.UUID, actor: Agent) -> None:
    if actor.role == AgentRole.admin:
        return
    if actor.id == experiment_creator_id:
        raise ForbiddenError("Experiment result must be reviewed by another agent")


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
    experiment.phase = ExperimentPhase.done

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
    _ensure_result_reviewer(experiment.creator_agent_id, actor)
    validate_phase_transition(experiment.phase, ExperimentPhase.running)
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
    experiment.phase = ExperimentPhase.running
    db.commit()
