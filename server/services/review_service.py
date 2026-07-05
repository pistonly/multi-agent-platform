import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from map_types.enums import ReviewSubstituteKind
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
from server.services import audit_service
from server.services.errors import ConflictError, ForbiddenError, NotFoundError, StateTransitionError
from server.services.project_service import get_experiment


class CreatorSelfReviewBlockedError(ForbiddenError):
    def __init__(
        self,
        message: str,
        *,
        actor_id: uuid.UUID,
        experiment_id: uuid.UUID,
    ) -> None:
        super().__init__(message)
        self.reason = "creator_self_review_blocked"
        self.actor_id = str(actor_id)
        self.experiment_id = str(experiment_id)


class ApproveEligibilityError(ConflictError):
    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def _qualifying_non_creator_reviews(reviews: list[Review], creator_id: uuid.UUID) -> list[Review]:
    return [
        review
        for review in reviews
        if review.reviewer_agent_id != creator_id
        and review.substitute_kind != ReviewSubstituteKind.admin_self_substitute
    ]


def compute_legacy_self_review(db: Session, experiment) -> bool:
    if experiment.phase in (ExperimentPhase.draft, ExperimentPhase.review):
        return False
    reviews = list(
        db.scalars(select(Review).where(Review.experiment_id == experiment.id))
    )
    return len(_qualifying_non_creator_reviews(reviews, experiment.creator_agent_id)) == 0


def _review_has_item_activity(review: Review) -> bool:
    for item in review.items:
        if item.kind != ReviewItemKind.unreasonable:
            continue
        if item.status is not None and item.status != ReviewItemStatus.open:
            return True
    return False


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


def count_open_status_unreasonable_for_experiment(
    db: Session, experiment_id: uuid.UUID
) -> int:
    """Unreasonable items with status=open only (plan revision obligation)."""
    stmt = (
        select(func.count())
        .select_from(ReviewItem)
        .join(Review)
        .where(
            Review.experiment_id == experiment_id,
            ReviewItem.kind == ReviewItemKind.unreasonable,
            ReviewItem.status == ReviewItemStatus.open,
        )
    )
    return db.scalar(stmt) or 0


def assert_approve_eligibility(db: Session, experiment) -> None:
    reviews = list(
        db.scalars(
            select(Review).where(
                Review.experiment_id == experiment.id,
                Review.plan_version == experiment.current_plan_version,
            )
        )
    )
    non_creator_reviews = _qualifying_non_creator_reviews(reviews, experiment.creator_agent_id)
    if not non_creator_reviews:
        if not reviews:
            raise ApproveEligibilityError(
                "Cannot approve: no review from a non-creator agent",
                reason="no_review",
            )
        raise ApproveEligibilityError(
            "Cannot approve: only creator reviews exist",
            reason="creator_only_review",
        )
    if count_open_unreasonable_for_experiment(db, experiment.id) > 0:
        raise ApproveEligibilityError(
            "Cannot approve: open unreasonable items remain",
            reason="open_unreasonable_item",
        )


def create_review(
    db: Session,
    experiment_id: uuid.UUID,
    reviewer: Agent,
    payload: ReviewCreate,
) -> Review:
    experiment = get_experiment(db, experiment_id)
    if experiment.phase != ExperimentPhase.review:
        raise StateTransitionError("Reviews can only be submitted during review phase")
    if (
        reviewer.id == experiment.creator_agent_id
        and reviewer.role != AgentRole.admin
    ):
        raise CreatorSelfReviewBlockedError(
            "Experiment creator cannot submit a review for their own experiment",
            actor_id=reviewer.id,
            experiment_id=experiment_id,
        )

    substitute_kind = ReviewSubstituteKind.none
    if reviewer.role == AgentRole.admin:
        if reviewer.id == experiment.creator_agent_id:
            substitute_kind = ReviewSubstituteKind.admin_self_substitute
        else:
            substitute_kind = ReviewSubstituteKind.admin_for_others
            if not payload.substitute_reason or not payload.substitute_reason.strip():
                raise ConflictError(
                    "Admin substitute review requires substitute_reason",
                )

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
        substitute_kind=substitute_kind,
    )
    db.add(review)
    db.flush()

    if substitute_kind != ReviewSubstituteKind.none:
        audit_service._log_no_commit(
            db,
            action="review_substitute",
            target_type="experiment",
            agent_id=reviewer.id,
            project_id=experiment.project_id,
            target_id=experiment_id,
            summary=f"Admin substitute review ({substitute_kind.value})",
            payload={
                "actor_id": str(reviewer.id),
                "actor_role": reviewer.role.value,
                "action": "review_substitute",
                "target": str(experiment_id),
                "target_plan_version": experiment.current_plan_version,
                "substitute_kind": substitute_kind.value,
                "reason": payload.substitute_reason,
                "review_id": str(review.id),
            },
        )

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


def withdraw_review(
    db: Session,
    experiment_id: uuid.UUID,
    review_id: uuid.UUID,
    actor: Agent,
) -> None:
    experiment = get_experiment(db, experiment_id)
    if experiment.phase != ExperimentPhase.review:
        raise StateTransitionError("Reviews can only be withdrawn during review phase")

    stmt = (
        select(Review)
        .where(Review.id == review_id, Review.experiment_id == experiment_id)
        .options(joinedload(Review.items))
    )
    review = db.scalar(stmt)
    if review is None:
        raise NotFoundError("Review not found")
    if review.reviewer_agent_id != actor.id and actor.role != AgentRole.admin:
        raise ForbiddenError("Only the review author can withdraw a review")
    if _review_has_item_activity(review):
        raise ConflictError("Cannot withdraw review after review items have been acted on")

    for item in review.items:
        db.delete(item)
    db.delete(review)
    db.commit()


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
        substitute_kind=review.substitute_kind,
        created_at=review.created_at,
        items=[ReviewItemRead.model_validate(i) for i in review.items],
    )
