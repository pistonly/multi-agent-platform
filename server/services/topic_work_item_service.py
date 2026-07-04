"""Unified per-agent topic work items (single source for todos + topic-progress)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from server.domain.models import Agent, Mention, MentionSourceType, Topic, TopicComment, TopicStatus
from server.domain.schemas import (
    PendingTopicReplyTodoRead,
    TopicProgressCommentRead,
    TopicProgressItemRead,
    TopicWorkItemRead,
)
from server.services import mention_service, topic_ack_service
from server.services.todo_service import _excerpt, _host_replied_after, thread_root_id
from server.services.topic_service import _agent_names_by_ids

_CLEAR_ACTION_BY_KIND = {
    "pending_topic_reply": "comment",
    "round_ack": "ack",
    "mention": "dismiss",
    "unread_change": "comment",
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
            .order_by(TopicComment.created_at.asc())
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


def _round_ack_items(db: Session, topic: Topic, agent: Agent) -> list[TopicWorkItem]:
    if not topic_ack_service.agent_needs_round_ack(db, topic, agent.id):
        return []
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


def _mention_items(db: Session, topic: Topic, agent: Agent) -> list[TopicWorkItem]:
    mentions = [
        m
        for m in mention_service.list_mentions_for_agent(db, agent.id, limit=200)
        if m.topic_id == topic.id and m.dismissed_at is None
    ]
    items: list[TopicWorkItem] = []
    for mention in mentions:
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


def _unread_change_items(
    topic: Topic,
    agent: Agent,
    comments: list[TopicComment],
) -> list[TopicWorkItem]:
    if not comments:
        return []
    last_comment = comments[-1]
    if last_comment.author_agent_id == agent.id:
        return []

    my_comments = [c for c in comments if c.author_agent_id == agent.id]
    my_last = my_comments[-1] if my_comments else None
    if my_last is None:
        new_comments = comments
    else:
        my_last_idx = next(i for i, c in enumerate(comments) if c.id == my_last.id)
        new_comments = comments[my_last_idx + 1 :]

    if not new_comments:
        return []

    items: list[TopicWorkItem] = []
    for comment in new_comments:
        items.append(
            TopicWorkItem(
                kind="unread_change",
                priority="contextual",
                topic_id=topic.id,
                topic_title=topic.title,
                source_comment_id=comment.id,
                thread_root_id=thread_root_id(comment.id, {c.id: c for c in comments}),
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
    if any(item.kind == "mention" for item in items):
        return True
    # Cold-start: contextual-only for never-participated agents (reviewer filter).
    return False


def topic_work_items_for_topic(
    db: Session,
    topic: Topic,
    agent: Agent,
) -> list[TopicWorkItem]:
    comments = _topic_comments(db, topic.id)
    items: list[TopicWorkItem] = []
    items.extend(_pending_reply_items(db, topic, agent, comments))
    items.extend(_round_ack_items(db, topic, agent))
    items.extend(_mention_items(db, topic, agent))
    items.extend(_unread_change_items(topic, agent, comments))
    if not _include_topic_for_agent(db, topic, agent, comments, items):
        return []
    return items


def topic_work_items_for_agent(db: Session, agent: Agent) -> list[TopicWorkItem]:
    if agent.project_id is None:
        return []
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
    items: list[TopicWorkItem] = []
    for topic in open_topics:
        items.extend(topic_work_items_for_topic(db, topic, agent))
    return items


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


def topic_progress_item_from_work_items(
    db: Session,
    topic: Topic,
    agent: Agent,
    items: list[TopicWorkItem],
) -> TopicProgressItemRead | None:
    topic_items = [i for i in items if i.topic_id == topic.id]
    if not topic_items:
        return None

    comments = _topic_comments(db, topic.id)
    if not comments:
        return None
    last_comment = comments[-1]
    my_comments = [c for c in comments if c.author_agent_id == agent.id]
    my_last = my_comments[-1] if my_comments else None

    author_names = _agent_names_by_ids(
        db,
        {last_comment.author_agent_id}
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
        last_comment_author_agent_id=last_comment.author_agent_id,
        last_comment_author_name=author_names.get(last_comment.author_agent_id),
        my_last_comment_id=my_last.id if my_last is not None else None,
        new_comments=new_comments,
        new_comment_count=len(new_comments),
        work_items=[item.to_read() for item in topic_items],
    )
