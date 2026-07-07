import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from server.domain.models import (
    Agent,
    AgentRole,
    ExperimentPhase,
    PlanVersion,
    Review,
    ReviewItem,
    ReviewItemKind,
    ReviewItemStatus,
)
from server.domain.schemas import PlanRevise
from server.domain.state_machine import ReviewItemTransitionContext, validate_review_item_transition
from server.services.errors import ForbiddenError, NotFoundError, StateTransitionError
from server.services.project_service import get_experiment


def list_plans(db: Session, experiment_id: uuid.UUID) -> list[PlanVersion]:
    get_experiment(db, experiment_id)
    stmt = (
        select(PlanVersion)
        .where(PlanVersion.experiment_id == experiment_id)
        .order_by(PlanVersion.version.asc())
    )
    return list(db.scalars(stmt))


def get_plan_version(db: Session, experiment_id: uuid.UUID, version: int) -> PlanVersion:
    get_experiment(db, experiment_id)
    stmt = select(PlanVersion).where(
        PlanVersion.experiment_id == experiment_id,
        PlanVersion.version == version,
    )
    plan = db.scalar(stmt)
    if plan is None:
        raise NotFoundError(f"Plan version {version} not found")
    return plan


def _count_unclosed_unreasonable_items(db: Session, experiment_id: uuid.UUID) -> int:
    stmt = (
        select(ReviewItem)
        .join(Review)
        .where(
            Review.experiment_id == experiment_id,
            ReviewItem.kind == ReviewItemKind.unreasonable,
            ReviewItem.status.in_(
                (
                    ReviewItemStatus.open,
                    ReviewItemStatus.addressed,
                    ReviewItemStatus.rebutted,
                    ReviewItemStatus.escalated,
                )
            ),
        )
    )
    return len(list(db.scalars(stmt)))


def revise_plan(
    db: Session,
    experiment_id: uuid.UUID,
    author: Agent,
    payload: PlanRevise,
) -> PlanVersion:
    experiment = get_experiment(db, experiment_id)
    if experiment.creator_agent_id != author.id and author.role != AgentRole.admin:
        raise ForbiddenError("Only the creator can revise the plan")
    if experiment.phase not in (
        ExperimentPhase.draft,
        ExperimentPhase.review,
        ExperimentPhase.running,
    ):
        raise StateTransitionError(
            "Plan can only be revised in draft, review, or running phase"
        )

    if not payload.addressed_item_ids and experiment.current_plan_version > 0:
        current_plan = db.scalar(
            select(PlanVersion).where(
                PlanVersion.experiment_id == experiment.id,
                PlanVersion.version == experiment.current_plan_version,
            )
        )
        if (
            current_plan is not None
            and current_plan.content_md == payload.content_md
            and _count_unclosed_unreasonable_items(db, experiment.id) == 0
        ):
            return current_plan

    new_version = experiment.current_plan_version + 1
    plan = PlanVersion(
        experiment_id=experiment.id,
        version=new_version,
        content_md=payload.content_md,
        author_agent_id=author.id,
        change_note=payload.change_note,
    )
    db.add(plan)
    experiment.current_plan_version = new_version

    if payload.addressed_item_ids:
        items_stmt = (
            select(ReviewItem)
            .where(ReviewItem.id.in_(payload.addressed_item_ids))
            .options(joinedload(ReviewItem.review))
        )
        items = list(db.scalars(items_stmt))
        if len(items) != len(payload.addressed_item_ids):
            raise NotFoundError("One or more review items not found")
        for item in items:
            if item.review.experiment_id != experiment.id:
                raise ForbiddenError("Review item does not belong to this experiment")
            if item.kind != ReviewItemKind.unreasonable:
                raise StateTransitionError("Only unreasonable items can be addressed")
            ctx = ReviewItemTransitionContext(
                is_creator=True,
                is_reviewer=False,
                is_admin=author.role == AgentRole.admin,
                via_plan_revision=True,
            )
            current = item.status or ReviewItemStatus.open
            validate_review_item_transition(current, ReviewItemStatus.addressed, ctx)
            item.status = ReviewItemStatus.addressed

    db.commit()
    db.refresh(plan)
    return plan
