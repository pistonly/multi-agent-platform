import uuid

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
    TopicActionItem,
    TopicActionItemStatus,
    TopicComment,
    TopicStatus,
)
from server.domain.schemas import (
    ExperimentSummaryRead,
    MentionTodoRead,
    PendingReplyRead,
    PendingTopicReplyTodoRead,
    TopicActionItemTodoRead,
    TodoRead,
)
from server.services import mention_service
from server.services import permissions as perm
from server.services.topic_service import topic_summaries_for_topics

_ACTIVE_PHASES = (
    ExperimentPhase.draft,
    ExperimentPhase.review,
    ExperimentPhase.approved,
    ExperimentPhase.running,
)
_REPLY_STATES = (ReviewItemStatus.addressed, ReviewItemStatus.rebutted)
_EXCERPT_LEN = 200


def _excerpt(body: str) -> str:
    text = body.strip().replace("\n", " ")
    if len(text) <= _EXCERPT_LEN:
        return text
    return text[: _EXCERPT_LEN - 1] + "…"


def thread_root_id(comment_id: uuid.UUID, by_id: dict[uuid.UUID, TopicComment]) -> uuid.UUID:
    """Walk parent_comment_id to the top-level comment; that id is the thread root."""
    current = by_id[comment_id]
    while current.parent_comment_id is not None:
        current = by_id[current.parent_comment_id]
    return current.id


def _host_replied_in_thread(
    thread_root: uuid.UUID,
    host_comment_ids: set[uuid.UUID],
    by_id: dict[uuid.UUID, TopicComment],
) -> bool:
    return any(thread_root_id(cid, by_id) == thread_root for cid in host_comment_ids)


def list_pending_topic_replies(db: Session, agent: Agent) -> list[PendingTopicReplyTodoRead]:
    open_topics = list(
        db.scalars(
            select(Topic)
            .where(
                Topic.creator_agent_id == agent.id,
                Topic.deleted_at.is_(None),
                Topic.archived_at.is_(None),
                Topic.status == TopicStatus.open,
            )
            .order_by(Topic.updated_at.desc())
        )
    )
    if not open_topics:
        return []

    topic_by_id = {t.id: t for t in open_topics}
    topic_ids = list(topic_by_id.keys())
    comments = list(
        db.scalars(
            select(TopicComment)
            .where(TopicComment.topic_id.in_(topic_ids))
            .options(joinedload(TopicComment.author))
            .order_by(TopicComment.created_at.asc())
        )
    )

    comments_by_topic: dict[uuid.UUID, list[TopicComment]] = {}
    for comment in comments:
        comments_by_topic.setdefault(comment.topic_id, []).append(comment)

    pending: list[PendingTopicReplyTodoRead] = []
    for topic_id, topic_comments in comments_by_topic.items():
        topic = topic_by_id[topic_id]
        by_id = {c.id: c for c in topic_comments}
        host_comment_ids = {c.id for c in topic_comments if c.author_agent_id == agent.id}

        for comment in topic_comments:
            if comment.author_agent_id == agent.id:
                continue
            root = thread_root_id(comment.id, by_id)
            if _host_replied_in_thread(root, host_comment_ids, by_id):
                continue
            pending.append(
                PendingTopicReplyTodoRead(
                    topic_id=topic.id,
                    topic_title=topic.title,
                    comment_id=comment.id,
                    parent_comment_id=comment.parent_comment_id,
                    thread_root_id=root,
                    author_agent_id=comment.author_agent_id,
                    author_name=comment.author.name if comment.author else None,
                    excerpt=_excerpt(comment.body),
                    created_at=comment.created_at,
                )
            )

    pending.sort(key=lambda p: p.created_at, reverse=True)
    return pending


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
                Topic.archived_at.is_(None),
                Topic.status == TopicStatus.open,
                # Hide topics the host has dismissed, unless new activity
                # (updated_at bumped past dismissed_at) has appeared since.
                or_(
                    Topic.dismissed_at.is_(None),
                    Topic.updated_at > Topic.dismissed_at,
                ),
            )
            .order_by(Topic.updated_at.desc())
        )
    )
    my_open_topics = topic_summaries_for_topics(db, open_topics, viewer_agent_id=agent.id)

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

    mention_service.reconcile_mentions_after_participation(db, agent.id)
    mention_rows = mention_service.list_mentions_for_agent(db, agent.id)
    author_ids = {m.author_agent_id for m in mention_rows}
    authors = {
        a.id: a.name
        for a in db.scalars(select(Agent).where(Agent.id.in_(author_ids))).all()
    } if author_ids else {}
    mentions = [
        MentionTodoRead(
            id=m.id,
            mentioned_agent_id=m.mentioned_agent_id,
            author_agent_id=m.author_agent_id,
            author_name=authors.get(m.author_agent_id),
            source_type=m.source_type.value,
            source_id=m.source_id,
            project_id=m.project_id,
            experiment_id=m.experiment_id,
            topic_id=m.topic_id,
            excerpt=m.excerpt,
            created_at=m.created_at,
            dismissed_at=m.dismissed_at,
        )
        for m in mention_rows
    ]

    pending_topic_replies = list_pending_topic_replies(db, agent)

    action_items = [
        TopicActionItemTodoRead(
            id=item.id,
            decision_id=item.decision_id,
            project_id=item.project_id,
            topic_id=item.topic_id,
            topic_title=item.topic.title if item.topic else "",
            title=item.title,
            description=item.description,
            status=item.status,
            due_at=item.due_at,
            linked_experiment_id=item.linked_experiment_id,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
        for item in db.scalars(
            select(TopicActionItem)
            .options(joinedload(TopicActionItem.topic))
            .where(
                TopicActionItem.owner_agent_id == agent.id,
                TopicActionItem.status == TopicActionItemStatus.open,
            )
            .order_by(TopicActionItem.updated_at.desc())
        )
    ]

    return TodoRead(
        my_open_experiments=my_open_experiments,
        pending_reviews=pending_reviews,
        pending_replies=pending_replies,
        pending_topic_replies=pending_topic_replies,
        my_open_topics=my_open_topics,
        mentions=mentions,
        action_items=action_items,
    )
