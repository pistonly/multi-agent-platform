"""Tests for topic progress API.

v0.13 M58: topics/comments fixtures are DB-direct inserts (write endpoints
retired); comments go through the service layer so round-ack semantics hold.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from server.domain.models import Agent
from tests._db_topic_factory import db_add_comment, db_create_topic

pytestmark = pytest.mark.slow


def _host(db):
    return db.scalar(select(Agent).where(Agent.name == "test-agent"))


def _reviewer(db):
    return db.scalar(select(Agent).where(Agent.name == "reviewer-agent"))


def _create_topic(db, project, **overrides):
    overrides.setdefault("title", "progress-topic")
    overrides.setdefault("description", "d")
    return db_create_topic(
        db,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=_host(db).id,
        **overrides,
    )


def test_topic_progress_empty_when_last_comment_is_mine(client, db_session, auth_headers, project):
    topic = _create_topic(db_session, project, title="progress-a")
    db_add_comment(db_session, topic_id=topic.id, author=_host(db_session), body="host opens")
    db_add_comment(db_session, topic_id=topic.id, author=_host(db_session), body="host again last")
    progress = client.get("/api/v1/agents/me/topic-progress", headers=auth_headers).json()
    assert progress["total"] == 0
    assert progress["items"] == []


def test_topic_progress_returns_comments_after_cursor(client, db_session, auth_headers, project, reviewer):
    topic = _create_topic(db_session, project, title="progress-b")
    tid = str(topic.id)
    db_add_comment(db_session, topic_id=topic.id, author=_host(db_session), body="host opens")
    db_add_comment(db_session, topic_id=topic.id, author=_reviewer(db_session), body="reviewer reply")
    db_add_comment(db_session, topic_id=topic.id, author=_host(db_session), body="host follow-up")
    client.post(
        f"/api/v1/agents/me/topics/{tid}/read",
        headers=auth_headers,
    )
    db_add_comment(db_session, topic_id=topic.id, author=_reviewer(db_session), body="reviewer newest")

    host_progress = client.get("/api/v1/agents/me/topic-progress", headers=auth_headers).json()
    assert host_progress["total"] == 1
    item = host_progress["items"][0]
    assert item["topic_id"] == tid
    assert item["new_comment_count"] == 1
    assert len(item["new_comments"]) == 1
    assert item["new_comments"][-1]["body"] == "reviewer newest"

    reviewer_progress = client.get(
        "/api/v1/agents/me/topic-progress", headers=reviewer["headers"]
    ).json()
    assert reviewer_progress["total"] == 1  # host follow-up unread until cursor read

    client.post(
        f"/api/v1/agents/me/topics/{tid}/read",
        headers=reviewer["headers"],
    )
    reviewer_after_read = client.get(
        "/api/v1/agents/me/topic-progress", headers=reviewer["headers"]
    ).json()
    assert reviewer_after_read["total"] == 0


def test_topic_progress_empty_when_never_participated(client, db_session, auth_headers, project, reviewer):
    topic = _create_topic(db_session, project, title="progress-c")
    db_add_comment(db_session, topic_id=topic.id, author=_host(db_session), body="only host so far")

    progress = client.get("/api/v1/agents/me/topic-progress", headers=reviewer["headers"]).json()
    assert progress["total"] == 0
