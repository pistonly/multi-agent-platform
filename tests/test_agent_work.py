"""Tests for GET /agents/me/work unified snapshot.

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
    overrides.setdefault("title", "work-topic")
    overrides.setdefault("description", "d")
    return db_create_topic(
        db,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=_host(db).id,
        **overrides,
    )


def test_agent_work_matches_split_endpoints(client, db_session, auth_headers, project, reviewer):
    topic = _create_topic(db_session, project, title="work-unified")
    db_add_comment(db_session, topic_id=topic.id, author=_host(db_session), body="host opens")
    db_add_comment(db_session, topic_id=topic.id, author=_reviewer(db_session), body="participant reply")

    work = client.get("/api/v1/agents/me/work", headers=auth_headers).json()
    todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    progress = client.get("/api/v1/agents/me/topic-progress", headers=auth_headers).json()
    me = client.get("/api/v1/agents/me", headers=auth_headers).json()

    assert work["agent"]["id"] == me["id"]
    assert work["agent"]["name"] == me["name"]
    assert work["todos"] == todos
    assert work["topic_progress"] == progress
    assert work["notifications"]["unread_count"] >= 0
    assert isinstance(work["notifications"]["items"], list)
    assert len(work["todos"]["pending_topic_replies"]) == 1
    assert work["topic_progress"]["total"] == 1


def test_agent_work_notification_category_wakeable_default(client, db_session, auth_headers, project):
    topic = _create_topic(db_session, project, title="work-notif")
    db_add_comment(db_session, topic_id=topic.id, author=_host(db_session), body="ping")

    wakeable = client.get("/api/v1/agents/me/work", headers=auth_headers).json()
    all_notifs = client.get(
        "/api/v1/agents/me/work",
        headers=auth_headers,
        params={"notification_category": "all"},
    ).json()

    assert wakeable["notifications"]["total"] <= all_notifs["notifications"]["total"]


def test_agent_work_empty_when_host_caught_up(client, db_session, auth_headers, project):
    topic = _create_topic(db_session, project, title="work-caught-up")
    db_add_comment(db_session, topic_id=topic.id, author=_host(db_session), body="only me")

    work = client.get("/api/v1/agents/me/work", headers=auth_headers).json()
    assert work["topic_progress"]["total"] == 0
    assert work["todos"]["pending_topic_replies"] == []
