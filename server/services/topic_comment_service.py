"""Topic comment CRUD and tree/seq helpers.

拆分自 ``topic_service.py``（P1 #1）以缓解 1100+ 行单文件可读性问题。
本模块只负责 topic 评论的写入与读取：
- ``create_topic_comment``：写评论 + bump ``topic.updated_at`` + 触发
  round-summary 检测 + mention 处理。
- ``list_topic_comments`` / ``topic_comment_read``：序列化（含 author_name、
  ``comment_seq`` 兜底、可选 tree）。
- ``_build_comment_tree`` / ``_ensure_comment_seq_values`` /
  ``_next_topic_comment_seq``：tree 组装与 seq 分配（兼容历史无 seq 行）。

``_get_topic`` 与 ``_agent_names_by_ids`` 是从 ``topic_service`` 复制的极短
helper（避免新模块依赖 topic_service 的私有 API 引入循环）；这两个函数
保持单行为，未来若 topic_service 公开了等价 API 可统一。

向后兼容：``topic_service`` 仍 re-export 本模块的公开函数，调用方
``from server.services.topic_service import create_topic_comment`` 无需修改。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from map_types.enums import AgentRole, TopicCommentKind
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.domain.models import Agent, Topic, TopicComment
from server.domain.schemas import (
    TopicCommentCreate,
    TopicCommentRead,
    TopicCommentTreeNode,
)
from server.services import mention_service, topic_ack_service
from server.services.errors import NotFoundError
from server.services.thread_activity import topic_comment_order_clauses
from server.services.topic_comment_kind import resolve_topic_comment_kind


def _get_topic(db: Session, topic_id: uuid.UUID) -> Topic:
    topic = db.get(Topic, topic_id)
    if topic is None or topic.deleted_at is not None:
        raise NotFoundError("Topic not found")
    return topic


def _agent_names_by_ids(db: Session, agent_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not agent_ids:
        return {}
    rows = db.execute(
        select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids))
    )
    return {agent_id: name for agent_id, name in rows}


def _comment_kind(comment: TopicComment) -> TopicCommentKind:
    return comment.kind or TopicCommentKind.user


def _build_comment_tree(
    comments: list[TopicComment],
    author_names: dict[uuid.UUID, str],
) -> list[TopicCommentTreeNode]:
    _ensure_comment_seq_values(comments)
    nodes: dict[uuid.UUID, TopicCommentTreeNode] = {}
    for comment in comments:
        nodes[comment.id] = TopicCommentTreeNode(
            id=comment.id,
            topic_id=comment.topic_id,
            author_agent_id=comment.author_agent_id,
            author_name=author_names.get(comment.author_agent_id),
            parent_comment_id=comment.parent_comment_id,
            body=comment.body,
            kind=_comment_kind(comment),
            comment_seq=comment.comment_seq,
            created_at=comment.created_at,
            children=[],
        )
    roots: list[TopicCommentTreeNode] = []
    for comment in comments:
        node = nodes[comment.id]
        if comment.parent_comment_id and comment.parent_comment_id in nodes:
            nodes[comment.parent_comment_id].children.append(node)
        else:
            roots.append(node)
    return roots


def _ensure_comment_seq_values(comments: list[TopicComment]) -> None:
    if all(comment.comment_seq is not None for comment in comments):
        return
    by_topic: dict[uuid.UUID, list[TopicComment]] = {}
    for comment in comments:
        by_topic.setdefault(comment.topic_id, []).append(comment)
    for topic_comments in by_topic.values():
        ordered = sorted(
            topic_comments,
            key=lambda comment: (
                comment.created_at,
                comment.comment_seq if comment.comment_seq is not None else 0,
                comment.id,
            ),
        )
        for index, comment in enumerate(ordered, start=1):
            if comment.comment_seq is None:
                comment.comment_seq = index


def _next_topic_comment_seq(db: Session, topic_id: uuid.UUID) -> int:
    current = db.scalar(
        select(func.coalesce(func.max(TopicComment.comment_seq), 0)).where(
            TopicComment.topic_id == topic_id
        )
    )
    return int(current or 0) + 1


def create_topic_comment(
    db: Session,
    topic_id: uuid.UUID,
    author: Agent,
    payload: TopicCommentCreate,
    *,
    commit: bool = True,
) -> tuple[TopicComment, list[str]]:
    topic = _get_topic(db, topic_id)
    if payload.parent_id is not None:
        parent = db.scalar(
            select(TopicComment).where(
                TopicComment.id == payload.parent_id, TopicComment.topic_id == topic_id
            )
        )
        if parent is None:
            raise NotFoundError("Parent comment not found")
    comment = TopicComment(
        topic_id=topic_id,
        author_agent_id=author.id,
        parent_comment_id=payload.parent_id,
        body=payload.body,
        kind=resolve_topic_comment_kind(payload.body),
        comment_seq=_next_topic_comment_seq(db, topic_id),
    )
    db.add(comment)
    # Bump topic.updated_at so any prior host-side dismiss on this topic
    # is automatically un-dismissed — there's new activity worth seeing.
    topic.updated_at = datetime.now(UTC)
    if (
        author.id == topic.creator_agent_id
        and payload.parent_id is None
        and topic_ack_service.is_round_summary_comment(payload.body)
    ):
        topic_ack_service.mark_round_ack_pending(topic)
    db.flush()
    db.refresh(comment)

    unresolved = mention_service.process_topic_comment_mentions(
        db, comment=comment, author=author, topic=topic, commit=False
    )
    mention_service.auto_dismiss_mentions_after_comment(
        db,
        new_comment_author=author,
        experiment_id=None,
        topic_id=topic_id,
        new_comment_id=comment.id,
        comment_body=payload.body,
        commit=False,
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return comment, unresolved


def topic_comment_read(
    db: Session, comment: TopicComment, *, unresolved_mentions: list[str] | None = None
) -> TopicCommentRead:
    author_names = _agent_names_by_ids(db, {comment.author_agent_id})
    _ensure_comment_seq_values([comment])
    return TopicCommentRead(
        id=comment.id,
        topic_id=comment.topic_id,
        author_agent_id=comment.author_agent_id,
        author_name=author_names.get(comment.author_agent_id),
        parent_comment_id=comment.parent_comment_id,
        body=comment.body,
        kind=_comment_kind(comment),
        comment_seq=comment.comment_seq,
        created_at=comment.created_at,
        unresolved_mentions=list(unresolved_mentions or ()),
    )


def list_topic_comments(
    db: Session,
    topic_id: uuid.UUID,
    *,
    tree: bool = False,
) -> list[TopicCommentRead] | list[TopicCommentTreeNode]:
    _get_topic(db, topic_id)
    stmt = (
        select(TopicComment)
        .where(TopicComment.topic_id == topic_id)
        .order_by(*topic_comment_order_clauses())
    )
    comments = list(db.scalars(stmt))
    _ensure_comment_seq_values(comments)
    author_names = _agent_names_by_ids(db, {comment.author_agent_id for comment in comments})
    if tree:
        return _build_comment_tree(comments, author_names)
    return [
        TopicCommentRead(
            id=comment.id,
            topic_id=comment.topic_id,
            author_agent_id=comment.author_agent_id,
            author_name=author_names.get(comment.author_agent_id),
            parent_comment_id=comment.parent_comment_id,
            body=comment.body,
            kind=_comment_kind(comment),
            comment_seq=comment.comment_seq,
            created_at=comment.created_at,
        )
        for comment in comments
    ]


# Suppress unused-import warning for AgentRole — re-exported for backwards
# compatibility with callers that imported it from topic_service.
__all__ = [
    "AgentRole",
    "create_topic_comment",
    "list_topic_comments",
    "topic_comment_read",
]
