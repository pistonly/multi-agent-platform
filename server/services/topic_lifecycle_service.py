"""Topic CRUD / summary / round lifecycle helpers.

拆分自 ``topic_service.py``。本模块负责：
- ``topic_summary`` / ``topic_summaries_for_topics`` 及评论聚合
- ``create_topic`` / ``list_topics`` / ``get_topic_detail``
- ``update_topic`` / ``soft_delete_topic`` / ``set_topic_status``
- ``advance_topic_round`` / ``record_participant_round_ack``
- ``dismiss_topic`` / ``mark_topic_read``

``get_topic_detail`` 组装 comments + decision，委托
``topic_comment_service`` 与 ``topic_resolve_service``。
``topic_service`` re-exports 保持 import 兼容。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from map_types.enums import ExperimentPhase, TopicDiscussionRound
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.domain.models import (
    Agent,
    Experiment,
    Topic,
    TopicComment,
    TopicReadCursor,
    TopicStatus,
)
from server.domain.schemas import (
    ExperimentSummaryRead,
    TopicCommentCreate,
    TopicCreate,
    TopicRead,
    TopicReadCursorRead,
    TopicSummaryRead,
    TopicUpdate,
)
from server.services import mention_service, topic_ack_service, topic_comment_service
from server.services._lookups import get_project
from server.services.errors import ConflictError, NotFoundError, StateTransitionError
from server.services.text_utils import excerpt
from server.services.thread_activity import topic_comment_order_clauses, topic_comment_order_clauses_desc
from server.services.topic_helpers import _agent_names_by_ids, _get_topic
from server.services.topic_resolve_service import _load_decision, topic_decision_read

_TOPIC_CLOSE_BLOCKING_EXPERIMENT_PHASES = (
    ExperimentPhase.draft,
    ExperimentPhase.review,
    ExperimentPhase.approved,
    ExperimentPhase.running,
    ExperimentPhase.result_review,
)

def topic_summary(db: Session, topic: Topic, *, viewer_agent_id: uuid.UUID | None = None) -> TopicSummaryRead:
    return topic_summaries_for_topics(db, [topic], viewer_agent_id=viewer_agent_id)[0]


def dismiss_topic(db: Session, *, agent: Agent, topic_id: uuid.UUID) -> Topic | None:
    """Host-only: hide an open topic from the creator's /todos.

    Re-surfaces automatically when the topic receives new activity
    (`updated_at` is bumped on new comments), so the dismiss is a soft
    "I've seen the current state" rather than a permanent archive.
    """
    topic = db.get(Topic, topic_id)
    if topic is None or topic.creator_agent_id != agent.id:
        return None
    if topic.dismissed_at is None:
        topic.dismissed_at = datetime.now(UTC)
        topic.dismissed_by_agent_id = agent.id
        db.commit()
        db.refresh(topic)
    return topic


def mark_topic_read(
    db: Session,
    *,
    agent: Agent,
    topic_id: uuid.UUID,
) -> TopicReadCursorRead:
    """Advance per-agent read cursor to the latest comment_seq (T3 D4).

    Independent transaction from comment INSERT (T3-1). Does not clear
    obligation work items (reply / mention / round_ack).
    """
    from server.services.permissions import ensure_topic_access

    topic = ensure_topic_access(db, agent, topic_id)
    max_seq = int(
        db.scalar(
            select(func.coalesce(func.max(TopicComment.comment_seq), 0)).where(
                TopicComment.topic_id == topic.id
            )
        )
        or 0
    )
    now = datetime.now(UTC)
    cursor = db.scalar(
        select(TopicReadCursor).where(
            TopicReadCursor.topic_id == topic.id,
            TopicReadCursor.agent_id == agent.id,
        )
    )
    if cursor is None:
        cursor = TopicReadCursor(
            topic_id=topic.id,
            agent_id=agent.id,
            last_read_comment_seq=max_seq,
        )
        db.add(cursor)
    else:
        cursor.last_read_comment_seq = max_seq
        cursor.updated_at = now
    db.commit()
    db.refresh(cursor)
    return TopicReadCursorRead(
        topic_id=cursor.topic_id,
        agent_id=cursor.agent_id,
        last_read_comment_seq=cursor.last_read_comment_seq,
        updated_at=cursor.updated_at,
    )


def topic_summaries_for_topics(
    db: Session,
    topics: list[Topic],
    *,
    viewer_agent_id: uuid.UUID | None = None,
) -> list[TopicSummaryRead]:
    if not topics:
        return []

    topic_ids = [topic.id for topic in topics]
    comment_counts = {
        topic_id: count
        for topic_id, count in db.execute(
            select(TopicComment.topic_id, func.count())
            .where(TopicComment.topic_id.in_(topic_ids))
            .group_by(TopicComment.topic_id)
        )
    }
    experiment_counts = {
        topic_id: count
        for topic_id, count in db.execute(
            select(Experiment.topic_id, func.count())
            .where(
                Experiment.topic_id.in_(topic_ids),
                Experiment.deleted_at.is_(None),
                Experiment.phase != ExperimentPhase.cancelled,
            )
            .group_by(Experiment.topic_id)
        )
    }
    creator_names = _agent_names_by_ids(db, {topic.creator_agent_id for topic in topics})
    latest_comments = _latest_topic_comments_by_topic(db, topic_ids)
    latest_author_names = _agent_names_by_ids(
        db, {comment.author_agent_id for comment in latest_comments.values()}
    )
    my_comment_counts: dict[uuid.UUID, int] = {}
    if viewer_agent_id is not None:
        my_comment_counts = _my_comment_counts_by_topic(db, topic_ids, viewer_agent_id)
    return [
        TopicSummaryRead(
            id=topic.id,
            project_id=topic.project_id,
            creator_agent_id=topic.creator_agent_id,
            creator_name=creator_names.get(topic.creator_agent_id),
            title=topic.title,
            description=topic.description,
            status=topic.status,
            pinned=topic.pinned,
            discussion_round=topic.discussion_round,
            round_summary_count=topic.round_summary_count,
            comment_count=comment_counts.get(topic.id, 0),
            experiment_count=experiment_counts.get(topic.id, 0),
            last_comment_id=latest_comments[topic.id].id if topic.id in latest_comments else None,
            last_comment_author_agent_id=(
                latest_comments[topic.id].author_agent_id if topic.id in latest_comments else None
            ),
            last_comment_author_name=(
                latest_author_names.get(latest_comments[topic.id].author_agent_id)
                if topic.id in latest_comments
                else None
            ),
            last_comment_excerpt=(
                excerpt(latest_comments[topic.id].body)
                if topic.id in latest_comments
                else None
            ),
            my_comment_count=my_comment_counts.get(topic.id, 0) if viewer_agent_id is not None else None,
            created_at=topic.created_at,
            updated_at=topic.updated_at,
            archived_at=topic.archived_at,
            dismissed_at=topic.dismissed_at,
            stale_since=topic.advance_round_pending_since,
            close_reason=topic.close_reason,
            close_note=topic.close_note,
        )
        for topic in topics
    ]


def _latest_topic_comments_by_topic(
    db: Session, topic_ids: list[uuid.UUID]
) -> dict[uuid.UUID, TopicComment]:
    """Return the latest TopicComment per topic in a single query.

    Previously this was an N+1 loop firing ``SELECT ... ORDER BY ... LIMIT 1``
    per topic. Topic list pages scale linearly with the number of topics, and
    the cost showed up on projects with many open topics. The replacement uses
    ``ROW_NUMBER() OVER (PARTITION BY topic_id ORDER BY <sort>)`` so each
    topic's winning row is selected in one round-trip.

    The ORDER BY mirrors :func:`thread_activity.topic_comment_order_clauses_desc`
    so the result is the same comment the old per-topic query would have picked.
    """
    if not topic_ids:
        return {}
    rn = (
        func.row_number()
        .over(
            partition_by=TopicComment.topic_id,
            order_by=topic_comment_order_clauses_desc(),
        )
        .label("rn")
    )
    subq = (
        select(TopicComment.id.label("cid"), rn)
        .where(TopicComment.topic_id.in_(topic_ids))
        .subquery()
    )
    winning_ids = db.scalars(
        select(subq.c.cid).where(subq.c.rn == 1)
    ).all()
    if not winning_ids:
        return {}
    comments = db.scalars(
        select(TopicComment).where(TopicComment.id.in_(winning_ids))
    ).all()
    return {comment.topic_id: comment for comment in comments}


def _my_comment_counts_by_topic(
    db: Session, topic_ids: list[uuid.UUID], agent_id: uuid.UUID
) -> dict[uuid.UUID, int]:
    if not topic_ids:
        return {}
    return {
        topic_id: count
        for topic_id, count in db.execute(
            select(TopicComment.topic_id, func.count())
            .where(
                TopicComment.topic_id.in_(topic_ids),
                TopicComment.author_agent_id == agent_id,
            )
            .group_by(TopicComment.topic_id)
        )
    }


def _latest_comment_authors_by_topic(
    db: Session, topic_ids: list[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    return {
        topic_id: comment.author_agent_id
        for topic_id, comment in _latest_topic_comments_by_topic(db, topic_ids).items()
    }

def create_topic(
    db: Session,
    project_id: uuid.UUID,
    creator_agent_id: uuid.UUID,
    payload: TopicCreate,
) -> Topic:
    get_project(db, project_id)
    creator = db.get(Agent, creator_agent_id)
    if creator is None:
        raise NotFoundError("Creator agent not found")
    topic = Topic(
        project_id=project_id,
        creator_agent_id=creator_agent_id,
        title=payload.title,
        description=payload.description,
        status=TopicStatus.open,
    )
    db.add(topic)
    db.flush()
    mention_service.process_topic_mentions(db, topic=topic, author=creator, commit=False)
    db.commit()
    db.refresh(topic)
    return topic


def list_topics(
    db: Session,
    project_id: uuid.UUID,
    *,
    status: TopicStatus | None = None,
    creator_agent_id: uuid.UUID | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 100,
    include_archived: bool = False,
    viewer_agent_id: uuid.UUID | None = None,
) -> tuple[list[TopicSummaryRead], int]:
    get_project(db, project_id)
    stmt = select(Topic).where(Topic.project_id == project_id, Topic.deleted_at.is_(None))
    if not include_archived:
        stmt = stmt.where(Topic.archived_at.is_(None))
    if status is not None:
        stmt = stmt.where(Topic.status == status)
    if creator_agent_id is not None:
        stmt = stmt.where(Topic.creator_agent_id == creator_agent_id)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(Topic.title.ilike(pattern) | Topic.description.ilike(pattern))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    stmt = stmt.order_by(Topic.pinned.desc(), Topic.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
    topics = list(db.scalars(stmt))
    return topic_summaries_for_topics(db, topics, viewer_agent_id=viewer_agent_id), total


def get_topic_detail(db: Session, topic_id: uuid.UUID) -> TopicRead:
    topic = _get_topic(db, topic_id)
    summary = topic_summary(db, topic)

    exp_stmt = (
        select(Experiment)
        .where(
            Experiment.topic_id == topic.id,
            Experiment.deleted_at.is_(None),
            Experiment.phase != ExperimentPhase.cancelled,
        )
        .order_by(Experiment.created_at.desc())
    )
    experiments = [ExperimentSummaryRead.model_validate(e) for e in db.scalars(exp_stmt)]
    comments = list(db.scalars(
        select(TopicComment)
        .where(TopicComment.topic_id == topic.id)
        .order_by(*topic_comment_order_clauses())
    ))
    comments_tree = topic_comment_service._build_comment_tree(comments, _agent_names_by_ids(
        db, {comment.author_agent_id for comment in comments}
    ))

    decision = _load_decision(db, topic.id)
    return TopicRead(
        **summary.model_dump(),
        experiments=experiments,
        comments=comments_tree,
        decision=topic_decision_read(db, decision) if decision is not None else None,
    )

def update_topic(db: Session, topic_id: uuid.UUID, payload: TopicUpdate) -> Topic:
    topic = _get_topic(db, topic_id)
    data = payload.model_dump(exclude_unset=True)
    archived = data.pop("archived", None)
    for key, value in data.items():
        setattr(topic, key, value)
    if archived is not None:
        topic.archived_at = datetime.now(UTC) if archived else None
    db.commit()
    db.refresh(topic)
    return topic


def soft_delete_topic(db: Session, topic_id: uuid.UUID) -> None:
    topic = _get_topic(db, topic_id)
    topic.deleted_at = datetime.now(UTC)
    db.commit()


def set_topic_status(
    db: Session,
    topic_id: uuid.UUID,
    target: TopicStatus,
    *,
    close_reason: str | None = None,
    close_note: str | None = None,
) -> Topic:
    topic = _get_topic(db, topic_id)
    if topic.status == target:
        return topic
    valid = (
        (target == TopicStatus.closed and topic.status == TopicStatus.open)
        or (target == TopicStatus.open and topic.status == TopicStatus.closed)
    )
    if not valid:
        raise StateTransitionError(f"Topic cannot move from {topic.status.value} to {target.value}")
    if target == TopicStatus.closed:
        blocking = db.scalar(
            select(Experiment).where(
                Experiment.topic_id == topic.id,
                Experiment.deleted_at.is_(None),
                Experiment.archived_at.is_(None),
                Experiment.phase.in_(_TOPIC_CLOSE_BLOCKING_EXPERIMENT_PHASES),
            )
        )
        if blocking is not None:
            raise ConflictError(
                "Cannot close topic while linked experiment "
                f"{blocking.id} is {blocking.phase.value}; complete or cancel it first"
            )
        topic.close_reason = close_reason
        topic.close_note = close_note
    else:
        # Reopen: clear close metadata so a future close starts fresh.
        topic.close_reason = None
        topic.close_note = None
    topic.status = target
    db.commit()
    db.refresh(topic)
    return topic


def advance_topic_round(
    db: Session,
    topic_id: uuid.UUID,
    *,
    increment_summary: bool = True,
    acknowledged_by: list[uuid.UUID] | None = None,
    mark_ready: bool = False,
    waive_ack: bool = False,
    waive_reason: str | None = None,
) -> Topic:
    topic = _get_topic(db, topic_id)
    if topic.status != TopicStatus.open:
        raise ConflictError("Cannot advance a closed topic")
    if topic.discussion_round == TopicDiscussionRound.ready:
        raise ConflictError("Topic discussion round is already ready")

    if waive_ack:
        # Host explicitly waives the participant ack requirement, recording a
        # reason instead of waiting for the 24h timeout.  The waiver is logged
        # as a system comment so the audit trail is self-contained.
        if not waive_reason or not waive_reason.strip():
            raise ConflictError("waive_ack requires a non-empty waive_reason")
        topic.advance_round_pending_since = None
    else:
        topic_ack_service.validate_advance_ack(
            db,
            topic,
            acknowledged_by=acknowledged_by or [],
        )

    current_count = topic.round_summary_count or 0
    next_count = current_count + 1 if increment_summary else current_count

    if mark_ready:
        # Host explicitly marks topic as ready for experiment creation.
        # Require at least one round summary before marking ready.
        if next_count < 1:
            raise ConflictError("Cannot mark ready before at least one round summary")
        topic.discussion_round = TopicDiscussionRound.ready
    else:
        # Advance to next round: round1 → round2 → round3 → ...
        # No upper bound — host decides when to mark ready.
        topic.discussion_round = TopicDiscussionRound.next_round(topic.discussion_round)

    topic.round_summary_count = next_count
    topic.advance_round_pending_since = None
    db.commit()
    db.refresh(topic)

    # After advancing to a new round (not mark_ready), auto-notify required
    # participants so the waker can wake them without host manually @mentioning.
    if not mark_ready:
        _notify_participants_round_advanced(db, topic)

    return topic


def _notify_participants_round_advanced(db: Session, topic: Topic) -> None:
    """Generate wakeable notifications for participants after round advancement.

    This replaces the manual ``@participant`` workaround that the Skill had to
    document as a "防死等" patch. Now the platform guarantees participants are
    woken whenever a new round starts.
    """
    from server.services import notification_service

    participant_ids = topic_ack_service.required_ack_agent_ids(db, topic)
    if not participant_ids:
        return

    notification_service.enqueue_for_agents(
        db,
        recipient_agent_ids=list(participant_ids),
        project_id=topic.project_id,
        actor_id=topic.creator_agent_id,
        event="topic.round_advanced",
        summary=f"话题「{topic.title}」已推进到 {topic.discussion_round}，请参与讨论",
        target_type="topic",
        target_id=topic.id,
        payload={
            "topic_id": str(topic.id),
            "discussion_round": topic.discussion_round,
            "round_summary_count": topic.round_summary_count,
        },
        wakeable=True,
        exclude_actor=True,
    )


def rollback_topic_round(db: Session, topic_id: uuid.UUID) -> Topic:
    """Roll the discussion round back by one step.

    Supported transitions:
    - ``ready`` → ``round{count}`` (undo ``mark_ready``)
    - ``roundN`` (N > 1) → ``round{N-1}`` (undo last advance)

    ``round1`` cannot be rolled back.  ``round_summary_count`` is decremented
    (floored at 0) so it stays consistent with the forward advance logic.
    ``advance_round_pending_since`` is cleared because the ack state of the
    previous round is no longer relevant.
    """
    topic = _get_topic(db, topic_id)
    if topic.status != TopicStatus.open:
        raise ConflictError("Cannot rollback a closed topic")

    current_round = topic.discussion_round
    current_count = topic.round_summary_count or 0

    if current_round == TopicDiscussionRound.ready:
        # Undo mark_ready: go back to the round that was current before ready.
        # When mark_ready was called, count was incremented, so the round
        # number equals the pre-decrement count.
        new_round = f"round{current_count}"
        new_count = max(current_count - 1, 0)
    else:
        prev = TopicDiscussionRound.prev_round(current_round)
        if prev is None:
            raise ConflictError("Cannot rollback beyond round1")
        new_round = prev
        new_count = max(current_count - 1, 0)

    topic.discussion_round = new_round
    topic.round_summary_count = new_count
    topic.advance_round_pending_since = None
    db.commit()
    db.refresh(topic)

    # Notify participants that the round was rolled back so they can re-engage.
    _notify_participants_round_rolled_back(db, topic)
    return topic


def _notify_participants_round_rolled_back(db: Session, topic: Topic) -> None:
    """Generate wakeable notifications for participants after round rollback."""
    from server.services import notification_service

    participant_ids = topic_ack_service.required_ack_agent_ids(db, topic)
    if not participant_ids:
        return

    notification_service.enqueue_for_agents(
        db,
        recipient_agent_ids=list(participant_ids),
        project_id=topic.project_id,
        actor_id=topic.creator_agent_id,
        event="topic.round_rolled_back",
        summary=f"话题「{topic.title}」已回退到 {topic.discussion_round}，请重新参与讨论",
        target_type="topic",
        target_id=topic.id,
        payload={
            "topic_id": str(topic.id),
            "discussion_round": topic.discussion_round,
            "round_summary_count": topic.round_summary_count,
        },
        wakeable=True,
        exclude_actor=True,
    )


def record_participant_round_ack(
    db: Session,
    topic_id: uuid.UUID,
    agent: Agent,
    kind: str,
) -> Topic:
    topic = _get_topic(db, topic_id)
    if topic.status != TopicStatus.open:
        raise ConflictError("Cannot ack a closed topic")
    if topic.archived_at is not None:
        raise ConflictError("Cannot ack an archived topic")
    if agent.id == topic.creator_agent_id:
        raise ConflictError("Host cannot post participant ack; use acknowledged_by when advancing")

    # ack 评论 + mention dismiss 写在同一事务内，单次 commit；任一步失败全部
    # 回滚，避免「ack 评论已写但 mention 未 dismiss」或重试时重复写多条 ack。
    topic_comment_service.create_topic_comment(
        db,
        topic_id,
        agent,
        TopicCommentCreate(body=topic_ack_service.participant_ack_body(kind)),
        commit=False,
    )
    if kind in {"accept", "dismiss"}:
        from server.services import mention_service

        mention_service.auto_dismiss_mentions_for_round_ack(
            db, topic=topic, agent=agent, ack_kind=kind, commit=False
        )
    db.commit()
    db.refresh(topic)
    return topic

