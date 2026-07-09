"""Unified per-agent topic work items (single source for todos + topic-progress)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from map_types.enums import TopicCommentKind
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from server.domain.models import Agent, Mention, MentionSourceType, Topic, TopicComment, TopicReadCursor, TopicStatus
from server.domain.schemas import (
    MentionTodoRead,
    PendingRoundAckTodoRead,
    PendingTopicReplyTodoRead,
    TopicProgressCommentRead,
    TopicProgressItemRead,
    TopicWorkItemRead,
)
from server.services import mention_service, topic_ack_service
from server.services.text_utils import excerpt as _excerpt
from server.services.thread_activity import (
    host_replied_after as _host_replied_after,
)
from server.services.thread_activity import (
    thread_root_id,
    topic_comment_order_clauses,
)
from server.services.topic_service import _agent_names_by_ids

_CLEAR_ACTION_BY_KIND = {
    "pending_topic_reply": "comment",
    "round_ack": "ack",
    "mention": "dismiss",
    "unread_change": "read",
}


@dataclass(frozen=True)
class TopicWorkItem:
    kind: str
    priority: str
    topic_id: uuid.UUID
    topic_title: str
    source_comment_id: uuid.UUID | None
    thread_root_id: uuid.UUID | None
    required_agent_id: uuid.UUID
    reason: str
    idempotency_key: str
    clear_action: str
    excerpt: str
    created_at: datetime
    discussion_round: str | None = None

    def to_read(self) -> TopicWorkItemRead:
        return TopicWorkItemRead(
            kind=self.kind,  # type: ignore[arg-type]
            priority=self.priority,  # type: ignore[arg-type]
            topic_id=self.topic_id,
            topic_title=self.topic_title,
            source_comment_id=self.source_comment_id,
            thread_root_id=self.thread_root_id,
            required_agent_id=self.required_agent_id,
            reason=self.reason,
            idempotency_key=self.idempotency_key,
            clear_action=self.clear_action,  # type: ignore[arg-type]
            excerpt=self.excerpt,
            created_at=self.created_at,
            discussion_round=self.discussion_round,
        )


def _topic_comments(db: Session, topic_id: uuid.UUID) -> list[TopicComment]:
    return list(
        db.scalars(
            select(TopicComment)
            .where(TopicComment.topic_id == topic_id)
            .order_by(*topic_comment_order_clauses())
        )
    )


def _topic_suppressed_by_dismiss(topic: Topic) -> bool:
    if topic.dismissed_at is None:
        return False
    updated = topic.updated_at
    dismissed = topic.dismissed_at
    if updated is None or dismissed is None:
        return topic.dismissed_at is not None
    return updated <= dismissed


def _agent_commented(comments: list[TopicComment], agent_id: uuid.UUID) -> bool:
    return any(c.author_agent_id == agent_id for c in comments)


def _pending_reply_items(
    db: Session,
    topic: Topic,
    agent: Agent,
    comments: list[TopicComment],
) -> list[TopicWorkItem]:
    if topic.creator_agent_id != agent.id:
        return []
    by_id = {c.id: c for c in comments}
    comment_order = [c.id for c in comments]
    host_comment_ids = {c.id for c in comments if c.author_agent_id == agent.id}
    items: list[TopicWorkItem] = []
    for comment in comments:
        if comment.author_agent_id == agent.id:
            continue
        # ack / round-summary protocol signals are system comments, not
        # conversational threads the host must reply to (compare
        # _unread_change_items, which already skips system comments).
        # They drive the dedicated round_ack / pending_advance_rounds
        # work items; counting them as pending_reply too leaves a stale
        # host obligation after every participant ack.
        if comment.kind == TopicCommentKind.system:
            continue
        root = thread_root_id(comment.id, by_id)
        if _host_replied_after(comment, host_comment_ids, by_id, comment_order=comment_order):
            continue
        items.append(
            TopicWorkItem(
                kind="pending_topic_reply",
                priority="obligation",
                topic_id=topic.id,
                topic_title=topic.title,
                source_comment_id=comment.id,
                thread_root_id=root,
                required_agent_id=agent.id,
                reason="thread_needs_reply",
                idempotency_key=f"pending_topic_reply:{topic.id}:{comment.id}",
                clear_action=_CLEAR_ACTION_BY_KIND["pending_topic_reply"],
                excerpt=_excerpt(comment.body),
                created_at=comment.created_at,
                discussion_round=str(topic.discussion_round),
            )
        )
    return items


def _round_ack_items(
    db: Session,
    topic: Topic,
    agent: Agent,
    comments: list[TopicComment] | None = None,
) -> list[TopicWorkItem]:
    if not topic_ack_service.agent_needs_round_ack(db, topic, agent.id):
        return []
    if comments is None:
        comments = _topic_comments(db, topic.id)
    summary = topic_ack_service.latest_host_round_summary_comment(
        comments,
        host_agent_id=topic.creator_agent_id,
    )
    excerpt = _excerpt(summary.body) if summary is not None else ""
    summary_id = summary.id if summary is not None else topic.id
    return [
        TopicWorkItem(
            kind="round_ack",
            priority="obligation",
            topic_id=topic.id,
            topic_title=topic.title,
            source_comment_id=summary.id if summary is not None else None,
            thread_root_id=summary.id if summary is not None else None,
            required_agent_id=agent.id,
            reason="round_summary_pending",
            idempotency_key=f"round_ack:{topic.id}:{summary_id}",
            clear_action=_CLEAR_ACTION_BY_KIND["round_ack"],
            excerpt=excerpt,
            created_at=summary.created_at if summary is not None else topic.updated_at,
            discussion_round=str(topic.discussion_round),
        )
    ]


def _mention_items(
    db: Session,
    topic: Topic,
    agent: Agent,
    mentions_for_agent: list[Mention] | None = None,
) -> list[TopicWorkItem]:
    mentions = mentions_for_agent
    if mentions is None:
        mentions = mention_service.list_mentions_for_agent(db, agent.id, limit=200)
    mentions = [
        m
        for m in mentions
        if m.topic_id == topic.id and m.dismissed_at is None
    ]
    items: list[TopicWorkItem] = []
    for mention in mentions:
        if mention_service.agent_replied_after_mention(
            db, mention=mention, agent_id=agent.id
        ):
            continue
        items.append(
            TopicWorkItem(
                kind="mention",
                priority="obligation",
                topic_id=topic.id,
                topic_title=topic.title,
                source_comment_id=mention.source_id if mention.source_type == MentionSourceType.topic_comment else None,
                thread_root_id=None,
                required_agent_id=agent.id,
                reason="mentioned",
                idempotency_key=f"mention:{mention.id}",
                clear_action=_CLEAR_ACTION_BY_KIND["mention"],
                excerpt=mention.excerpt,
                created_at=mention.created_at,
                discussion_round=str(topic.discussion_round),
            )
        )
    return items


def _agent_cursor_seq(db: Session, topic_id: uuid.UUID, agent_id: uuid.UUID) -> int:
    cursor = db.scalar(
        select(TopicReadCursor.last_read_comment_seq).where(
            TopicReadCursor.topic_id == topic_id,
            TopicReadCursor.agent_id == agent_id,
        )
    )
    return int(cursor or 0)


def _unread_change_items(
    db: Session,
    topic: Topic,
    agent: Agent,
    comments: list[TopicComment],
) -> list[TopicWorkItem]:
    if not comments:
        return []
    cursor_seq = _agent_cursor_seq(db, topic.id, agent.id)
    new_comments = [
        c
        for c in comments
        if c.comment_seq > cursor_seq and c.author_agent_id != agent.id
    ]
    if not new_comments:
        return []

    by_id = {c.id: c for c in comments}
    items: list[TopicWorkItem] = []
    for comment in new_comments:
        if comment.kind == TopicCommentKind.system:
            continue
        items.append(
            TopicWorkItem(
                kind="unread_change",
                priority="contextual",
                topic_id=topic.id,
                topic_title=topic.title,
                source_comment_id=comment.id,
                thread_root_id=thread_root_id(comment.id, by_id),
                required_agent_id=agent.id,
                reason="content_since_cursor",
                idempotency_key=f"unread_change:{topic.id}:{comment.id}",
                clear_action=_CLEAR_ACTION_BY_KIND["unread_change"],
                excerpt=_excerpt(comment.body),
                created_at=comment.created_at,
                discussion_round=str(topic.discussion_round),
            )
        )
    return items


def _include_topic_for_agent(
    db: Session,
    topic: Topic,
    agent: Agent,
    comments: list[TopicComment],
    items: list[TopicWorkItem],
) -> bool:
    if not items:
        return False
    if topic.creator_agent_id == agent.id and _topic_suppressed_by_dismiss(topic):
        return False
    if any(item.priority == "obligation" for item in items):
        return True
    if _agent_commented(comments, agent.id):
        return True
    if topic_ack_service.agent_needs_round_ack(db, topic, agent.id):
        return True
    # Cold-start: contextual-only for never-participated agents (reviewer filter).
    return any(item.kind == "mention" for item in items)


def topic_work_items_for_topic(
    db: Session,
    topic: Topic,
    agent: Agent,
    *,
    comments: list[TopicComment] | None = None,
    mentions_for_agent: list[Mention] | None = None,
) -> list[TopicWorkItem]:
    if comments is None:
        comments = _topic_comments(db, topic.id)
    items: list[TopicWorkItem] = []
    items.extend(_pending_reply_items(db, topic, agent, comments))
    items.extend(_round_ack_items(db, topic, agent, comments))
    items.extend(_mention_items(db, topic, agent, mentions_for_agent))
    items.extend(_unread_change_items(db, topic, agent, comments))
    if not _include_topic_for_agent(db, topic, agent, comments, items):
        return []
    return items


@dataclass(frozen=True)
class AgentTopicWorkItems:
    items: list[TopicWorkItem]
    comments_by_topic: dict[uuid.UUID, list[TopicComment]]


def topic_work_items_for_agent(db: Session, agent: Agent) -> list[TopicWorkItem]:
    return topic_work_items_bundle_for_agent(db, agent).items


def topic_work_items_bundle_for_agent(db: Session, agent: Agent) -> AgentTopicWorkItems:
    if agent.project_id is None:
        return AgentTopicWorkItems(items=[], comments_by_topic={})
    open_topics = list(
        db.scalars(
            select(Topic)
            .where(
                Topic.project_id == agent.project_id,
                Topic.deleted_at.is_(None),
                Topic.archived_at.is_(None),
                Topic.status == TopicStatus.open,
            )
            .order_by(Topic.updated_at.desc())
        )
    )
    mentions_for_agent = mention_service.list_mentions_for_agent(db, agent.id, limit=200)
    items: list[TopicWorkItem] = []
    comments_by_topic: dict[uuid.UUID, list[TopicComment]] = {}
    for topic in open_topics:
        comments = _topic_comments(db, topic.id)
        comments_by_topic[topic.id] = comments
        items.extend(
            topic_work_items_for_topic(
                db,
                topic,
                agent,
                comments=comments,
                mentions_for_agent=mentions_for_agent,
            )
        )
    return AgentTopicWorkItems(items=items, comments_by_topic=comments_by_topic)


def obligation_items_for_agent(db: Session, agent: Agent) -> list[TopicWorkItem]:
    return [item for item in topic_work_items_for_agent(db, agent) if item.priority == "obligation"]


def pending_topic_replies_from_work_items(
    db: Session,
    items: list[TopicWorkItem],
) -> list[PendingTopicReplyTodoRead]:
    comment_ids = [
        item.source_comment_id for item in items if item.kind == "pending_topic_reply" and item.source_comment_id
    ]
    if not comment_ids:
        return []
    comments = {
        c.id: c
        for c in db.scalars(
            select(TopicComment)
            .where(TopicComment.id.in_(comment_ids))
            .options(joinedload(TopicComment.author))
        )
    }
    author_names = _agent_names_by_ids(
        db,
        {c.author_agent_id for c in comments.values()},
    )
    rows: list[PendingTopicReplyTodoRead] = []
    for item in items:
        if item.kind != "pending_topic_reply" or item.source_comment_id is None:
            continue
        if item.thread_root_id is None:
            continue
        comment = comments.get(item.source_comment_id)
        if comment is None:
            continue
        parent_id = comment.parent_comment_id
        rows.append(
            PendingTopicReplyTodoRead(
                topic_id=item.topic_id,
                topic_title=item.topic_title,
                comment_id=item.source_comment_id,
                parent_comment_id=parent_id,
                thread_root_id=item.thread_root_id,
                author_agent_id=comment.author_agent_id,
                author_name=author_names.get(comment.author_agent_id),
                excerpt=item.excerpt,
                created_at=item.created_at,
            )
        )
    rows.sort(key=lambda p: p.created_at, reverse=True)
    return rows


def pending_round_acks_from_work_items(
    db: Session,
    items: list[TopicWorkItem],
) -> list[PendingRoundAckTodoRead]:
    ack_items = [item for item in items if item.kind == "round_ack"]
    if not ack_items:
        return []
    topic_ids = {item.topic_id for item in ack_items}
    topics = {
        t.id: t
        for t in db.scalars(select(Topic).where(Topic.id.in_(topic_ids)))
    }
    rows: list[PendingRoundAckTodoRead] = []
    for item in ack_items:
        topic = topics.get(item.topic_id)
        if topic is None:
            continue
        rows.append(
            PendingRoundAckTodoRead(
                topic_id=item.topic_id,
                topic_title=item.topic_title,
                discussion_round=topic.discussion_round,
                round_summary_count=int(topic.round_summary_count or 0),
                summary_comment_id=item.source_comment_id,
                summary_excerpt=item.excerpt or None,
                advance_round_pending_since=topic.advance_round_pending_since,
                updated_at=topic.updated_at,
            )
        )
    rows.sort(key=lambda p: p.updated_at, reverse=True)
    return rows


def mentions_from_work_items(
    db: Session,
    agent_id: uuid.UUID,
    items: list[TopicWorkItem],
) -> list[MentionTodoRead]:
    mention_ids: list[uuid.UUID] = []
    for item in items:
        if item.kind != "mention":
            continue
        prefix = "mention:"
        if not item.idempotency_key.startswith(prefix):
            continue
        mention_ids.append(uuid.UUID(item.idempotency_key[len(prefix) :]))
    if not mention_ids:
        return []
    mentions = list(
        db.scalars(
            select(Mention).where(
                Mention.id.in_(mention_ids),
                Mention.mentioned_agent_id == agent_id,
                Mention.dismissed_at.is_(None),
            )
        )
    )
    author_ids = {m.author_agent_id for m in mentions}
    author_names = _agent_names_by_ids(db, author_ids)
    return [
        MentionTodoRead(
            id=m.id,
            mentioned_agent_id=m.mentioned_agent_id,
            author_agent_id=m.author_agent_id,
            author_name=author_names.get(m.author_agent_id),
            source_type=m.source_type.value,
            source_id=m.source_id,
            project_id=m.project_id,
            experiment_id=m.experiment_id,
            topic_id=m.topic_id,
            excerpt=m.excerpt,
            created_at=m.created_at,
            dismissed_at=m.dismissed_at,
        )
        for m in mentions
    ]


def topic_progress_item_from_work_items(
    db: Session,
    topic: Topic,
    agent: Agent,
    items: list[TopicWorkItem],
    *,
    comments: list[TopicComment] | None = None,
) -> TopicProgressItemRead | None:
    topic_items = [i for i in items if i.topic_id == topic.id]
    if not topic_items:
        return None

    if comments is None:
        comments = _topic_comments(db, topic.id)
    last_comment = comments[-1] if comments else None
    my_comments = [c for c in comments if c.author_agent_id == agent.id]
    my_last = my_comments[-1] if my_comments else None

    author_names = _agent_names_by_ids(
        db,
        ({last_comment.author_agent_id} if last_comment is not None else set())
        | {c.author_agent_id for c in comments if c.author_agent_id != agent.id},
    )

    unread = [i for i in topic_items if i.kind == "unread_change"]
    new_comments = []
    for item in unread:
        if item.source_comment_id is None:
            continue
        comment = next((c for c in comments if c.id == item.source_comment_id), None)
        if comment is None:
            continue
        new_comments.append(
            TopicProgressCommentRead(
                id=comment.id,
                author_agent_id=comment.author_agent_id,
                author_name=author_names.get(comment.author_agent_id),
                parent_comment_id=comment.parent_comment_id,
                body=comment.body,
                excerpt=item.excerpt,
                created_at=comment.created_at,
            )
        )

    return TopicProgressItemRead(
        topic_id=topic.id,
        topic_title=topic.title,
        discussion_round=topic.discussion_round,
        last_comment_author_agent_id=last_comment.author_agent_id if last_comment is not None else None,
        last_comment_author_name=author_names.get(last_comment.author_agent_id) if last_comment is not None else None,
        my_last_comment_id=my_last.id if my_last is not None else None,
        new_comments=new_comments,
        new_comment_count=len(new_comments),
        work_items=[item.to_read() for item in topic_items],
    )
