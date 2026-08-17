"""P0: same-second host thread reply clears pending_topic_reply via comment_seq order.

v0.13 M58: topic fixture is a DB-direct insert; comments go through the
service layer so threading (parent_id) and comment_seq allocation stay intact.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from server.domain.models import Agent, TopicComment
from tests._db_topic_factory import db_add_comment, db_create_topic


def test_same_second_host_reply_clears_pending_topic_reply(
    client, auth_headers, project, reviewer, db_session
):
    host = db_session.scalar(select(Agent).where(Agent.name == "test-agent"))
    participant = db_session.scalar(select(Agent).where(Agent.name == "reviewer-agent"))
    topic = db_create_topic(
        db_session,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=host.id,
        title="same-second-host-reply",
        description="d",
    )

    participant_comment = db_add_comment(
        db_session, topic_id=topic.id, author=participant, body="participant needs host reply"
    )
    host_reply = db_add_comment(
        db_session,
        topic_id=topic.id,
        author=host,
        body="host replies in thread",
        parent_id=participant_comment.id,
    )

    stamp = datetime(2026, 7, 5, 1, 0, 0, tzinfo=timezone.utc)
    ids = [participant_comment.id, host_reply.id]
    for row in db_session.scalars(select(TopicComment).where(TopicComment.id.in_(ids))):
        row.created_at = stamp
    db_session.commit()
    db_session.expire_all()

    assert int(participant_comment.comment_seq) < int(host_reply.comment_seq)

    todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    assert todos["pending_topic_replies"] == []

    progress = client.get("/api/v1/agents/me/topic-progress", headers=auth_headers).json()
    reply_work = [
        w
        for item in progress.get("items", [])
        for w in item.get("work_items", [])
        if w.get("kind") == "pending_topic_reply"
    ]
    assert reply_work == []
