from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

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
    TopicStatus,
)
from server.domain.schemas import (
    ExperimentSummaryRead,
    MentionTodoRead,
    PendingAdvanceRoundTodoRead,
    PendingPlanRevisionRead,
    PendingReplyRead,
    PendingRoundAckTodoRead,
    PendingTopicReplyTodoRead,
    StaleOpenTopicTodoRead,
    TodoRead,
    TopicActionItemTodoRead,
)
from server.services import mention_service, topic_ack_service
from server.services import permissions as perm
from server.services.topic_service import topic_summaries_for_topics

if TYPE_CHECKING:
    from server.services.topic_work_item_service import AgentTopicWorkItems

_ACTIVE_PHASES = (
    ExperimentPhase.draft,
    ExperimentPhase.review,
    ExperimentPhase.approved,
    ExperimentPhase.running,
    ExperimentPhase.result_review,
)
_REPLY_STATES = (ReviewItemStatus.addressed, ReviewItemStatus.rebutted)
STALE_OPEN_TOPIC_THRESHOLD = timedelta(minutes=30)


def list_pending_topic_replies(
    db: Session,
    agent: Agent,
    *,
    work_items: list | None = None,
) -> list[PendingTopicReplyTodoRead]:
    from server.services import topic_work_item_service as work_items_module

    if work_items is None:
        work_items = work_items_module.topic_work_items_for_agent(db, agent)
    items = [item for item in work_items if item.kind == "pending_topic_reply"]
    return work_items_module.pending_topic_replies_from_work_items(db, items)


def list_pending_round_acks(
    db: Session,
    agent: Agent,
    *,
    work_items: list | None = None,
) -> list[PendingRoundAckTodoRead]:
    """Round summaries awaiting this agent's ack — projected from topic work items."""
    from server.services import topic_work_item_service as work_items_module

    if work_items is None:
        work_items = work_items_module.topic_work_items_for_agent(db, agent)
    items = [item for item in work_items if item.kind == "round_ack"]
    return work_items_module.pending_round_acks_from_work_items(db, items)


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


def list_stale_open_topics(
    db: Session,
    agent: Agent,
    *,
    pending_topic_replies: list[PendingTopicReplyTodoRead] | None = None,
    pending_advance_rounds: list[PendingAdvanceRoundTodoRead] | None = None,
    now: datetime | None = None,
    threshold_minutes: int | None = None,
) -> list[StaleOpenTopicTodoRead]:
    """Host-owned open topics that need periodic follow-up.

    The threshold is configurable per-call (test injects a small value);
    when omitted it falls back to ``server.config.settings.stale_open_topic_threshold_minutes``
    (env: ``MAP_STALE_OPEN_TOPIC_THRESHOLD_MINUTES``, default 30). The
    legacy module-level ``STALE_OPEN_TOPIC_THRESHOLD`` constant remains
    as the final fallback so callers that pre-date settings still get
    the historical 30-minute default.
    """
    if agent.project_id is None:
        return []

    if threshold_minutes is None:
        try:
            from server.config import get_settings

            threshold_minutes = get_settings().stale_open_topic_threshold_minutes
        except Exception:
            threshold_minutes = int(STALE_OPEN_TOPIC_THRESHOLD.total_seconds() // 60)
    if threshold_minutes < 0:
        raise ValueError("threshold_minutes must be >= 0")

    now = now or datetime.now(UTC)
    cutoff = now - timedelta(minutes=threshold_minutes)
    suppressed_topic_ids = {
        item.topic_id for item in (pending_topic_replies or [])
    } | {
        item.topic_id for item in (pending_advance_rounds or [])
    }
    open_topics = list(
        db.scalars(
            select(Topic)
            .where(
                Topic.creator_agent_id == agent.id,
                Topic.project_id == agent.project_id,
                Topic.deleted_at.is_(None),
                Topic.archived_at.is_(None),
                Topic.status == TopicStatus.open,
                Topic.updated_at <= cutoff,
                or_(
                    Topic.dismissed_at.is_(None),
                    Topic.updated_at > Topic.dismissed_at,
                ),
            )
            .order_by(Topic.updated_at.asc())
        )
    )
    rows: list[StaleOpenTopicTodoRead] = []
    for topic in open_topics:
        if topic.id in suppressed_topic_ids:
            continue
        rows.append(
            StaleOpenTopicTodoRead(
                topic_id=topic.id,
                topic_title=topic.title,
                discussion_round=topic.discussion_round,
                round_summary_count=int(topic.round_summary_count or 0),
                stale_since=topic.updated_at,
                updated_at=topic.updated_at,
            )
        )
    return rows


def _experiment_summary_with_open_unreasonable(
    db: Session,
    experiment: Experiment,
    agent: Agent,
) -> ExperimentSummaryRead:
    from server.services.experiment_capabilities_service import experiment_summary_for_actor
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

    return experiment_summary_for_actor(
        db,
        experiment,
        agent,
        extra_updates={
            "open_unreasonable_count": count_open_unreasonable_for_experiment(
                db, experiment.id
            ),
            "log_count": log_count,
            "latest_log_summary": latest.summary if latest else None,
        },
    )


def list_pending_plan_revisions(db: Session, agent: Agent) -> list[PendingPlanRevisionRead]:
    """Host/creator experiments in review with open unreasonable items needing plan revise."""
    from server.services.review_service import count_open_status_unreasonable_for_experiment

    stmt = (
        select(Experiment)
        .where(
            Experiment.creator_agent_id == agent.id,
            Experiment.deleted_at.is_(None),
            Experiment.phase == ExperimentPhase.review,
        )
        .order_by(Experiment.updated_at.desc())
    )
    if not perm.is_admin(agent):
        stmt = stmt.where(Experiment.project_id == agent.project_id)

    pending: list[PendingPlanRevisionRead] = []
    for experiment in db.scalars(stmt):
        open_count = count_open_status_unreasonable_for_experiment(db, experiment.id)
        if open_count <= 0:
            continue
        pending.append(
            PendingPlanRevisionRead(
                experiment_id=experiment.id,
                experiment_title=experiment.title,
                current_plan_version=experiment.current_plan_version,
                open_unreasonable_count=open_count,
                updated_at=experiment.updated_at,
            )
        )
    return pending


def get_todos(
    db: Session,
    agent: Agent,
    *,
    bundle: "AgentTopicWorkItems | None" = None,
    include_all_partitions: bool = False,
) -> TodoRead:
    my_open_experiments = [
        _experiment_summary_with_open_unreasonable(db, e, agent)
        for e in db.scalars(
            select(Experiment)
            .where(
                Experiment.creator_agent_id == agent.id,
                Experiment.deleted_at.is_(None),
                Experiment.archived_at.is_(None),
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
    # DEPRECATED (T3 D6): prefer topic-progress work_items; kept for host dismiss (explicit_only).

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

    from server.services.experiment_capabilities_service import experiment_summary_for_actor

    pending_reviews = [
        experiment_summary_for_actor(db, exp, agent) for exp in db.scalars(review_stmt)
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
        experiment_summary_for_actor(db, exp, agent)
        for exp in db.scalars(result_review_stmt)
    ]

    reply_stmt = (
        select(ReviewItem)
        .join(Review, ReviewItem.review_id == Review.id)
        .join(Experiment, Review.experiment_id == Experiment.id)
        .options(joinedload(ReviewItem.review).joinedload(Review.experiment))
        .where(
            ReviewItem.status.in_(_REPLY_STATES),
            Experiment.deleted_at.is_(None),
            Review.archived_at.is_(None),
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

    from server.services import todo_persona_filter as persona_filter
    from server.services import topic_work_item_service as work_items

    shows_review_obligations = persona_filter.agent_sees_review_obligations(
        agent, include_all_partitions=include_all_partitions
    )
    if not shows_review_obligations:
        pending_reviews = []
        pending_result_reviews = []
        experiment_review_informational = persona_filter.list_experiment_review_informational(
            db, agent
        )
    else:
        experiment_review_informational = []

    if bundle is None:
        bundle = work_items.topic_work_items_bundle_for_agent(db, agent)
    all_work_items = bundle.items
    mention_work_items = [item for item in all_work_items if item.kind == "mention"]
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

    pending_topic_replies = list_pending_topic_replies(db, agent, work_items=all_work_items)
    pending_round_acks = list_pending_round_acks(db, agent, work_items=all_work_items)
    pending_advance_rounds = list_pending_advance_rounds(db, agent)
    stale_open_topics = list_stale_open_topics(
        db,
        agent,
        pending_topic_replies=pending_topic_replies,
        pending_advance_rounds=pending_advance_rounds,
    )
    pending_plan_revisions = list_pending_plan_revisions(db, agent)

    action_item_rows = list(
        db.scalars(
            select(TopicActionItem)
            .options(joinedload(TopicActionItem.topic))
            .where(
                TopicActionItem.owner_agent_id == agent.id,
                TopicActionItem.status == TopicActionItemStatus.open,
            )
            .order_by(TopicActionItem.updated_at.desc())
        )
    )
    # 批量预取 linked experiment phase，避免序列化时 N+1；同时让 todos 消费者
    # 能区分"等 reviewer 审批"（phase=result_review）与"host 自身待办"。
    from server.services.topic_service import _linked_experiment_phases_batch

    phase_map = _linked_experiment_phases_batch(db, action_item_rows)
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
            linked_experiment_phase=phase_map.get(item.id),
            wake_count=item.wake_count,
            first_open_at=item.first_open_at,
            last_woken_at=item.last_woken_at,
            stale_at=item.stale_at,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
        for item in action_item_rows
    ]

    return TodoRead(
        my_open_experiments=my_open_experiments,
        pending_reviews=pending_reviews,
        pending_result_reviews=pending_result_reviews,
        experiment_review_informational=experiment_review_informational,
        pending_replies=pending_replies,
        pending_plan_revisions=pending_plan_revisions,
        pending_topic_replies=pending_topic_replies,
        pending_round_acks=pending_round_acks,
        pending_advance_rounds=pending_advance_rounds,
        stale_open_topics=stale_open_topics,
        my_open_topics=my_open_topics,
        mentions=mentions,
        action_items=action_items,
    )
