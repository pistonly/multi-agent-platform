import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from server.domain.models import (
    Agent,
    AgentRole,
    ExperimentPhase,
    Review,
    ReviewItem,
    ReviewItemKind,
    ReviewItemStatus,
)
from server.domain.schemas import ReviewCreate, ReviewItemRead, ReviewItemUpdate, ReviewRead
from server.domain.state_machine import ReviewItemTransitionContext, validate_review_item_transition
from server.services.errors import ConflictError, ForbiddenError, NotFoundError, StateTransitionError
from server.services.project_service import get_experiment


def get_unreasonable_items(db: Session, experiment_id: uuid.UUID) -> list[ReviewItem]:
    stmt = (
        select(ReviewItem)
        .join(Review)
        .where(
            Review.experiment_id == experiment_id,
            ReviewItem.kind == ReviewItemKind.unreasonable,
        )
        .options(joinedload(ReviewItem.review))
    )
    return list(db.scalars(stmt))


def count_open_unreasonable_for_experiment(db: Session, experiment_id: uuid.UUID) -> int:
    stmt = (
        select(func.count())
        .select_from(ReviewItem)
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
    return db.scalar(stmt) or 0


def create_review(
    db: Session,
    experiment_id: uuid.UUID,
    reviewer: Agent,
    payload: ReviewCreate,
) -> Review:
    experiment = get_experiment(db, experiment_id)
    if experiment.phase != ExperimentPhase.review:
        raise StateTransitionError("Reviews can only be submitted during review phase")

    existing = db.scalar(
        select(Review).where(
            Review.experiment_id == experiment_id,
            Review.reviewer_agent_id == reviewer.id,
            Review.plan_version == experiment.current_plan_version,
        )
    )
    if existing:
        raise ConflictError("Reviewer already submitted a review for this plan version")

    review = Review(
        experiment_id=experiment_id,
        reviewer_agent_id=reviewer.id,
        plan_version=experiment.current_plan_version,
    )
    db.add(review)
    db.flush()

    for content in payload.reasonable_items:
        db.add(
            ReviewItem(
                review_id=review.id,
                kind=ReviewItemKind.reasonable,
                content=content,
                status=None,
            )
        )
    for content in payload.unreasonable_items:
        db.add(
            ReviewItem(
                review_id=review.id,
                kind=ReviewItemKind.unreasonable,
                content=content,
                status=ReviewItemStatus.open,
            )
        )

    db.commit()
    stmt = select(Review).where(Review.id == review.id).options(joinedload(Review.items))
    reloaded = db.scalar(stmt)
    assert reloaded is not None
    return reloaded


def list_reviews(db: Session, experiment_id: uuid.UUID) -> list[Review]:
    get_experiment(db, experiment_id)
    stmt = (
        select(Review)
        .where(Review.experiment_id == experiment_id)
        .options(joinedload(Review.items))
        .order_by(Review.created_at.asc())
    )
    return list(db.scalars(stmt).unique())


def get_review_item(db: Session, item_id: uuid.UUID) -> ReviewItem:
    stmt = select(ReviewItem).where(ReviewItem.id == item_id).options(joinedload(ReviewItem.review))
    item = db.scalar(stmt)
    if item is None:
        raise NotFoundError("Review item not found")
    return item


def update_review_item(
    db: Session,
    item_id: uuid.UUID,
    actor: Agent,
    payload: ReviewItemUpdate,
) -> ReviewItem:
    item = get_review_item(db, item_id)
    experiment = get_experiment(db, item.review.experiment_id)

    if item.kind != ReviewItemKind.unreasonable:
        raise StateTransitionError("Only unreasonable items have mutable status")
    if item.status is None:
        raise StateTransitionError("Item has no status")

    is_creator = experiment.creator_agent_id == actor.id
    is_reviewer = item.review.reviewer_agent_id == actor.id
    is_admin = actor.role == AgentRole.admin

    ctx = ReviewItemTransitionContext(
        is_creator=is_creator,
        is_reviewer=is_reviewer,
        is_admin=is_admin,
    )
    try:
        validate_review_item_transition(item.status, payload.status, ctx)
    except Exception as exc:
        raise StateTransitionError(str(exc)) from exc

    item.status = payload.status
    item.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(item)
    return item


def review_to_read(review: Review) -> ReviewRead:
    return ReviewRead(
        id=review.id,
        experiment_id=review.experiment_id,
        reviewer_agent_id=review.reviewer_agent_id,
        plan_version=review.plan_version,
        created_at=review.created_at,
        items=[ReviewItemRead.model_validate(i) for i in review.items],
    )
