"""Per-agent experiment actions and blocked_on (experiment plan v2 AC#3)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from server.domain.models import (
    Agent,
    AgentRole,
    Experiment,
    ExperimentPhase,
    Review,
    ReviewItem,
    ReviewItemKind,
    ReviewItemStatus,
)
from server.domain.schemas import ExperimentDetailRead, ExperimentSummaryRead
from server.services.review_service import (
    _qualifying_non_creator_reviews,
    _review_has_item_activity,
    compute_legacy_self_review,
    count_open_unreasonable_for_experiment,
    has_review_on_older_plan_version,
)

_REPLY_STATES = (ReviewItemStatus.addressed, ReviewItemStatus.rebutted)


def _reviews_for_current_plan(db: Session, experiment: Experiment) -> list[Review]:
    return list(
        db.scalars(
            select(Review)
            .where(
                Review.experiment_id == experiment.id,
                Review.plan_version == experiment.current_plan_version,
            )
            .options(joinedload(Review.items))
        ).unique()
    )


def _has_non_creator_review(reviews: list[Review], creator_id: uuid.UUID) -> bool:
    return len(_qualifying_non_creator_reviews(reviews, creator_id)) > 0


def _actor_review_for_current_plan(reviews: list[Review], actor_id: uuid.UUID) -> Review | None:
    for review in reviews:
        if review.reviewer_agent_id == actor_id:
            return review
    return None


def _count_open_status_unreasonable(db: Session, experiment_id: uuid.UUID) -> int:
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


def _reviewer_has_pending_addressed_items(
    db: Session, experiment_id: uuid.UUID, actor_id: uuid.UUID
) -> bool:
    stmt = (
        select(func.count())
        .select_from(ReviewItem)
        .join(Review)
        .where(
            Review.experiment_id == experiment_id,
            Review.reviewer_agent_id == actor_id,
            ReviewItem.kind == ReviewItemKind.unreasonable,
            ReviewItem.status.in_(_REPLY_STATES),
        )
    )
    return (db.scalar(stmt) or 0) > 0


def compute_experiment_capabilities(
    db: Session, experiment: Experiment, actor: Agent
) -> tuple[list[str], str | None]:
    is_creator = actor.id == experiment.creator_agent_id
    is_admin = actor.role == AgentRole.admin
    phase = experiment.phase

    actions: list[str] = []
    blocked_on: str | None = None

    if phase == ExperimentPhase.result_review:
        if is_creator:
            return [], "awaiting_result_approval"
        return ["accept_result", "reject_result"], "none"

    if _reviewer_has_pending_addressed_items(db, experiment.id, actor.id):
        return ["resolve_item"], "awaiting_addressed_item_ack"

    if phase == ExperimentPhase.draft:
        if is_creator or is_admin:
            return ["submit_for_review"], "none"
        return actions, blocked_on

    if phase == ExperimentPhase.review:
        reviews = _reviews_for_current_plan(db, experiment)
        if is_creator or is_admin:
            open_unreasonable = count_open_unreasonable_for_experiment(db, experiment.id)
            if open_unreasonable > 0:
                blocked_on = "open_unreasonable_item"
                if _count_open_status_unreasonable(db, experiment.id) > 0:
                    actions.append("plan_revise")
            elif not _has_non_creator_review(reviews, experiment.creator_agent_id):
                if has_review_on_older_plan_version(db, experiment):
                    blocked_on = "awaiting_review_for_current_plan_version"
                else:
                    blocked_on = "awaiting_non_creator_review"
            else:
                blocked_on = "none"
                actions = ["approve", "withdraw"]
            return actions, blocked_on

        if actor.id != experiment.creator_agent_id:
            actor_review = _actor_review_for_current_plan(reviews, actor.id)
            if actor_review is None:
                return ["review_add"], "none"
            if not _review_has_item_activity(actor_review):
                return ["review_withdraw"], "none"
        return actions, blocked_on

    if is_creator or is_admin:
        if phase == ExperimentPhase.approved:
            return ["start"], "none"
        if phase == ExperimentPhase.running:
            return ["complete"], "none"

    return actions, blocked_on


def experiment_summary_for_actor(
    db: Session,
    experiment: Experiment,
    actor: Agent,
    *,
    extra_updates: dict | None = None,
) -> ExperimentSummaryRead:
    actions, blocked_on = compute_experiment_capabilities(db, experiment, actor)
    legacy = compute_legacy_self_review(db, experiment)
    update: dict = {
        "actions": actions,
        "blocked_on": blocked_on,
        "legacy_self_review": legacy,
    }
    if extra_updates:
        update.update(extra_updates)
    return ExperimentSummaryRead.model_validate(experiment).model_copy(update=update)


def apply_capabilities_to_detail(
    detail: ExperimentDetailRead,
    actions: list[str],
    blocked_on: str | None,
    *,
    legacy_self_review: bool = False,
) -> ExperimentDetailRead:
    return detail.model_copy(
        update={
            "actions": actions,
            "blocked_on": blocked_on,
            "legacy_self_review": legacy_self_review,
        }
    )
