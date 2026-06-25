from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session, joinedload

from server.domain.models import (
    Agent,
    Experiment,
    ExperimentPhase,
    Review,
    ReviewItem,
    ReviewItemStatus,
    Topic,
    TopicStatus,
)
from server.domain.schemas import (
    ExperimentSummaryRead,
    PendingReplyRead,
    TodoRead,
)
from server.services import permissions as perm
from server.services.topic_service import topic_summaries_for_topics

_ACTIVE_PHASES = (
    ExperimentPhase.draft,
    ExperimentPhase.review,
    ExperimentPhase.approved,
    ExperimentPhase.running,
)
_REPLY_STATES = (ReviewItemStatus.addressed, ReviewItemStatus.rebutted)


def get_todos(db: Session, agent: Agent) -> TodoRead:
    my_open_experiments = [
        ExperimentSummaryRead.model_validate(e)
        for e in db.scalars(
            select(Experiment)
            .where(
                Experiment.creator_agent_id == agent.id,
                Experiment.deleted_at.is_(None),
                Experiment.phase.in_(_ACTIVE_PHASES),
            )
            .order_by(Experiment.updated_at.desc())
        )
    ]

    open_topics = list(
        db.scalars(
            select(Topic)
            .where(
                Topic.creator_agent_id == agent.id,
                Topic.deleted_at.is_(None),
                Topic.status == TopicStatus.open,
            )
            .order_by(Topic.updated_at.desc())
        )
    )
    my_open_topics = topic_summaries_for_topics(db, open_topics)

    project_clause = None if perm.is_admin(agent) else Experiment.project_id == agent.project_id

    reviewed = exists().where(
        Review.experiment_id == Experiment.id,
        Review.reviewer_agent_id == agent.id,
    )
    review_stmt = (
        select(Experiment)
        .where(
            Experiment.deleted_at.is_(None),
            Experiment.phase == ExperimentPhase.review,
            ~reviewed,
        )
        .order_by(Experiment.updated_at.desc())
    )
    if project_clause is not None:
        review_stmt = review_stmt.where(project_clause)

    pending_reviews = [
        ExperimentSummaryRead.model_validate(exp) for exp in db.scalars(review_stmt)
    ]

    reply_stmt = (
        select(ReviewItem)
        .join(Review, ReviewItem.review_id == Review.id)
        .join(Experiment, Review.experiment_id == Experiment.id)
        .options(joinedload(ReviewItem.review).joinedload(Review.experiment))
        .where(
            ReviewItem.status.in_(_REPLY_STATES),
            Experiment.deleted_at.is_(None),
            or_(
                Experiment.creator_agent_id == agent.id,
                Review.reviewer_agent_id == agent.id,
            ),
        )
    )
    if project_clause is not None:
        reply_stmt = reply_stmt.where(project_clause)

    pending_replies = [
        PendingReplyRead(
            item_id=item.id,
            experiment_id=item.review.experiment_id,
            experiment_title=item.review.experiment.title,
            content=item.content,
            status=item.status,
            updated_at=item.updated_at,
        )
        for item in db.scalars(reply_stmt)
    ]

    return TodoRead(
        my_open_experiments=my_open_experiments,
        pending_reviews=pending_reviews,
        pending_replies=pending_replies,
        my_open_topics=my_open_topics,
    )
