import uuid
from datetime import UTC, datetime

from map_types.enums import ReviewArchivedReason
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


def _archive_prior_version_reviews(
    db: Session,
    *,
    experiment_id: uuid.UUID,
    new_version: int,
) -> int:
    """Mark every review whose plan_version < new_version as archived.

    Called from ``revise_plan`` after ``experiment.current_plan_version``
    is bumped. The cascade is idempotent: rows already carrying
    ``archived_at`` are skipped so manual / superseded archives stay
    untouched, and the trigger can safely run on repeat bumps without
    overwriting audit metadata.

    Returns the number of rows newly archived in this call (used by
    end-to-end tests; service callers may ignore).
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    stmt = select(Review).where(
        Review.experiment_id == experiment_id,
        Review.plan_version < new_version,
        Review.archived_at.is_(None),
    )
    rows = list(db.scalars(stmt))
    for review in rows:
        review.archived_at = now
        review.archived_reason = ReviewArchivedReason.auto
    return len(rows)


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

    # a764abf6 I1.(a): enforce plan frontmatter lint at revise time so
    # missing required fields raise STATE_MACHINE_PLAN_MARKER_MISSING
    # before any version bump / archive cascade runs.
    from server.services.plan_marker_service import assert_plan_frontmatter_ok

    assert_plan_frontmatter_ok(payload.content_md)

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

    # I1(b) — auto-archive prior reviews: every review whose plan_version
    # is less than the new current_plan_version is no longer canonical and
    # should be marked archived_reason='auto' + archived_at=now().
    # The early-return branch above (line 82-94) skips this on purpose:
    # when content is unchanged and no items are addressed, no version bump
    # happens so no archive cascade is appropriate.
    _archive_prior_version_reviews(db, experiment_id=experiment.id, new_version=new_version)

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
