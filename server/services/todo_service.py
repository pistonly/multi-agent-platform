import uuid

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, joinedload

from server.domain.models import (
    Agent,
    Experiment,
    ExperimentLog,
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
    PendingRoundAckTodoRead,
    PendingAdvanceRoundTodoRead,
    PendingTopicReplyTodoRead,
    TopicActionItemTodoRead,
    TodoRead,
)
from server.services import mention_service
from server.services import permissions as perm
from server.services import topic_ack_service
from server.services.topic_service import topic_summaries_for_topics

_ACTIVE_PHASES = (
    ExperimentPhase.draft,
    ExperimentPhase.review,
    ExperimentPhase.approved,
    ExperimentPhase.running,
    ExperimentPhase.result_review,
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


def _host_replied_after(
    comment: TopicComment,
    host_comment_ids: set[uuid.UUID],
    by_id: dict[uuid.UUID, TopicComment],
    *,
    comment_order: list[uuid.UUID] | None = None,
) -> bool:
    """True when the host has posted in the same thread after ``comment``.

    Uses chronological comment order (``created_at`` asc, stable tie-break) so a
    host reply clears the whole thread without treating an earlier host opener as
    a reply to later participant messages. Same-second timestamps rely on list
    order rather than strict ``created_at > cutoff``.
    """
    root = thread_root_id(comment.id, by_id)
    if comment_order is None:
        comment_order = sorted(
            by_id.keys(),
            key=lambda cid: (by_id[cid].created_at, str(cid)),
        )
    try:
        comment_pos = comment_order.index(comment.id)
    except ValueError:
        return False
    for cid in host_comment_ids:
        if thread_root_id(cid, by_id) != root:
            continue
        try:
            host_pos = comment_order.index(cid)
        except ValueError:
            continue
        if host_pos > comment_pos:
            return True
    return False


def list_pending_topic_replies(db: Session, agent: Agent) -> list[PendingTopicReplyTodoRead]:
    from server.services import topic_work_item_service as work_items

    items = [
        item
        for item in work_items.topic_work_items_for_agent(db, agent)
        if item.kind == "pending_topic_reply"
    ]
    return work_items.pending_topic_replies_from_work_items(db, items)


def list_pending_round_acks(db: Session, agent: Agent) -> list[PendingRoundAckTodoRead]:
    """Round summaries awaiting this agent's ack — projected from topic work items."""
    from server.services import topic_work_item_service as work_items

    items = [
        item
        for item in work_items.topic_work_items_for_agent(db, agent)
        if item.kind == "round_ack"
    ]
    return work_items.pending_round_acks_from_work_items(db, items)


def list_pending_advance_rounds(db: Session, agent: Agent) -> list[PendingAdvanceRoundTodoRead]:
    """Topics the host created where all required acks are in and advance-round is due."""
    if agent.project_id is None:
        return []

    open_topics = list(
        db.scalars(
            select(Topic)
            .where(
                Topic.creator_agent_id == agent.id,
                Topic.project_id == agent.project_id,
                Topic.deleted_at.is_(None),
                Topic.archived_at.is_(None),
                Topic.status == TopicStatus.open,
            )
            .order_by(Topic.updated_at.desc())
        )
    )
    pending: list[PendingAdvanceRoundTodoRead] = []
    for topic in open_topics:
        if topic_ack_service.advance_round_ack_state(db, topic) != "ready":
            continue
        pending.append(
            PendingAdvanceRoundTodoRead(
                topic_id=topic.id,
                topic_title=topic.title,
                discussion_round=topic.discussion_round,
                round_summary_count=int(topic.round_summary_count or 0),
                advance_round_pending_since=topic.advance_round_pending_since,
                updated_at=topic.updated_at,
            )
        )
    return pending


def _experiment_summary_with_open_unreasonable(
    db: Session,
    experiment: Experiment,
) -> ExperimentSummaryRead:
    from server.services.log_service import get_latest_log
    from server.services.review_service import count_open_unreasonable_for_experiment

    log_count = (
        db.scalar(
            select(func.count())
            .select_from(ExperimentLog)
            .where(ExperimentLog.experiment_id == experiment.id)
        )
        or 0
    )
    latest = get_latest_log(db, experiment.id)

    return ExperimentSummaryRead.model_validate(experiment).model_copy(
        update={
            "open_unreasonable_count": count_open_unreasonable_for_experiment(
                db, experiment.id
            ),
            "log_count": log_count,
            "latest_log_summary": latest.summary if latest else None,
        }
    )


def get_todos(db: Session, agent: Agent) -> TodoRead:
    my_open_experiments = [
        _experiment_summary_with_open_unreasonable(db, e)
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
        Review.plan_version == Experiment.current_plan_version,
    )
    review_stmt = (
        select(Experiment)
        .where(
            Experiment.deleted_at.is_(None),
            Experiment.phase == ExperimentPhase.review,
            Experiment.creator_agent_id != agent.id,
            ~reviewed,
        )
        .order_by(Experiment.updated_at.desc())
    )
    if project_clause is not None:
        review_stmt = review_stmt.where(project_clause)

    pending_reviews = [
        ExperimentSummaryRead.model_validate(exp) for exp in db.scalars(review_stmt)
    ]

    result_review_stmt = (
        select(Experiment)
        .where(
            Experiment.deleted_at.is_(None),
            Experiment.phase == ExperimentPhase.result_review,
            Experiment.creator_agent_id != agent.id,
        )
        .order_by(Experiment.updated_at.desc())
    )
    if project_clause is not None:
        result_review_stmt = result_review_stmt.where(project_clause)

    pending_result_reviews = [
        ExperimentSummaryRead.model_validate(exp) for exp in db.scalars(result_review_stmt)
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

    from server.services import topic_work_item_service as work_items

    mention_work_items = [
        item
        for item in work_items.obligation_items_for_agent(db, agent)
        if item.kind == "mention"
    ]
    mentions = work_items.mentions_from_work_items(db, agent.id, mention_work_items)
    topic_mention_ids = {m.id for m in mentions}
    for m in mention_service.list_mentions_for_agent(db, agent.id):
        if m.id in topic_mention_ids:
            continue
        if m.experiment_id is None:
            continue
        if mention_service.agent_replied_after_mention(db, mention=m, agent_id=agent.id):
            continue
        author_name = db.scalar(select(Agent.name).where(Agent.id == m.author_agent_id))
        mentions.append(
            MentionTodoRead(
                id=m.id,
                mentioned_agent_id=m.mentioned_agent_id,
                author_agent_id=m.author_agent_id,
                author_name=author_name,
                source_type=m.source_type.value,
                source_id=m.source_id,
                project_id=m.project_id,
                experiment_id=m.experiment_id,
                topic_id=m.topic_id,
                excerpt=m.excerpt,
                created_at=m.created_at,
                dismissed_at=m.dismissed_at,
            )
        )

    pending_topic_replies = list_pending_topic_replies(db, agent)
    pending_round_acks = list_pending_round_acks(db, agent)
    pending_advance_rounds = list_pending_advance_rounds(db, agent)

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
            wake_count=item.wake_count,
            first_open_at=item.first_open_at,
            last_woken_at=item.last_woken_at,
            stale_at=item.stale_at,
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
        pending_result_reviews=pending_result_reviews,
        pending_replies=pending_replies,
        pending_topic_replies=pending_topic_replies,
        pending_round_acks=pending_round_acks,
        pending_advance_rounds=pending_advance_rounds,
        my_open_topics=my_open_topics,
        mentions=mentions,
        action_items=action_items,
    )
