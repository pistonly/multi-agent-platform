"""Thread / container activity helpers (T3 D3 single module)."""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Comment, Mention, MentionSourceType, TopicComment


class _CommentLike(Protocol):
    id: uuid.UUID
    author_agent_id: uuid.UUID
    parent_comment_id: uuid.UUID | None
    created_at: object


def thread_root_id(
    comment_id: uuid.UUID,
    by_id: dict[uuid.UUID, TopicComment],
) -> uuid.UUID:
    """Walk parent_comment_id to the top-level comment; that id is the thread root."""
    current = by_id[comment_id]
    while current.parent_comment_id is not None:
        current = by_id[current.parent_comment_id]
    return current.id


def host_replied_after(
    comment: TopicComment,
    host_comment_ids: set[uuid.UUID],
    by_id: dict[uuid.UUID, TopicComment],
    *,
    comment_order: list[uuid.UUID] | None = None,
) -> bool:
    """True when the host posted in the same thread after ``comment``."""
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


def _is_reply_in_thread_to(
    comment: TopicComment | Comment,
    source: TopicComment | Comment,
    by_id: dict[uuid.UUID, TopicComment | Comment],
) -> bool:
    cur: TopicComment | Comment | None = comment
    while cur is not None and cur.parent_comment_id is not None:
        if cur.parent_comment_id == source.id:
            return True
        cur = by_id.get(cur.parent_comment_id)
    return False


def comment_after(
    db: Session,
    *,
    mention: Mention,
    agent_id: uuid.UUID,
) -> bool:
    """True when ``agent_id`` posted in the same container after the mention source."""
    if mention.source_type == MentionSourceType.topic_comment and mention.topic_id is not None:
        comments = list(
            db.scalars(
                select(TopicComment)
                .where(TopicComment.topic_id == mention.topic_id)
                .order_by(TopicComment.created_at.asc(), TopicComment.comment_seq.asc(), TopicComment.id.asc())
            )
        )
    elif (
        mention.source_type == MentionSourceType.experiment_comment
        and mention.experiment_id is not None
    ):
        comments = list(
            db.scalars(
                select(Comment)
                .where(Comment.experiment_id == mention.experiment_id)
                .order_by(Comment.created_at.asc(), Comment.id.asc())
            )
        )
    else:
        return False

    by_id = {comment.id: comment for comment in comments}
    source = by_id.get(mention.source_id)
    if source is None:
        return False

    for comment in comments:
        if comment.author_agent_id != agent_id:
            continue
        if comment.id == mention.source_id:
            continue
        if comment.created_at > source.created_at:
            return True
        if comment.created_at < source.created_at:
            continue
        if _is_reply_in_thread_to(comment, source, by_id):
            return True
        source_seq = getattr(source, "comment_seq", None)
        comment_seq = getattr(comment, "comment_seq", None)
        if source_seq is not None and comment_seq is not None:
            if comment_seq > source_seq:
                return True
            continue
        if comment.id > source.id:
            return True
    return False


def replied_after(
    db: Session,
    *,
    mention: Mention,
    agent_id: uuid.UUID,
) -> bool:
    """Alias for ``comment_after`` (plan naming)."""
    return comment_after(db, mention=mention, agent_id=agent_id)
