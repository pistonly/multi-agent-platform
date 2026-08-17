"""T3: comment_seq cursor read API and obligation preservation.

v0.13 M58: topics/comments fixtures are DB-direct inserts (write endpoints
retired); comments go through the service layer so mention handling and
comment_seq allocation stay intact.
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
    overrides.setdefault("title", "cursor-topic")
    overrides.setdefault("description", "d")
    return db_create_topic(
        db,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=_host(db).id,
        **overrides,
    )


def test_topic_read_advances_cursor_and_clears_unread_progress(
    client, db_session, auth_headers, project, reviewer
):
    topic = _create_topic(db_session, project, title="read-cursor")
    tid = str(topic.id)
    db_add_comment(db_session, topic_id=topic.id, author=_host(db_session), body="host opens")
    db_add_comment(db_session, topic_id=topic.id, author=_reviewer(db_session), body="reviewer says hi")

    reviewer_headers = reviewer["headers"]
    progress_before = client.get(
        "/api/v1/agents/me/topic-progress", headers=reviewer_headers
    ).json()
    assert progress_before["total"] == 1
    assert progress_before["items"][0]["new_comment_count"] >= 1

    read_resp = client.post(
        f"/api/v1/agents/me/topics/{tid}/read",
        headers=reviewer_headers,
    )
    assert read_resp.status_code == 200, read_resp.text
    body = read_resp.json()
    assert body["topic_id"] == tid
    assert body["last_read_comment_seq"] >= 2

    progress_after = client.get(
        "/api/v1/agents/me/topic-progress", headers=reviewer_headers
    ).json()
    assert progress_after["total"] == 0


def test_topic_read_does_not_clear_mention_obligation(client, db_session, auth_headers, reviewer, project):
    """Cursor read must not dismiss mention obligation (T3-2)."""
    topic = _create_topic(db_session, project, title="read-mention")
    tid = str(topic.id)
    db_add_comment(db_session, topic_id=topic.id, author=_host(db_session), body="@reviewer-agent please review")

    reviewer_headers = reviewer["headers"]
    todos_before = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert len(todos_before["mentions"]) == 1

    read_resp = client.post(
        f"/api/v1/agents/me/topics/{tid}/read",
        headers=reviewer_headers,
    )
    assert read_resp.status_code == 200, read_resp.text

    todos_after = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert len(todos_after["mentions"]) == 1

    progress = client.get("/api/v1/agents/me/topic-progress", headers=reviewer_headers).json()
    mention_work = [
        w
        for item in progress.get("items", [])
        for w in item.get("work_items", [])
        if w.get("kind") == "mention" and w.get("priority") == "obligation"
    ]
    assert len(mention_work) == 1


def test_comment_seq_strictly_monotonic_per_topic(client, db_session, auth_headers, project):
    topic = _create_topic(db_session, project, title="seq-mono")
    seqs = []
    for i in range(3):
        comment = db_add_comment(db_session, topic_id=topic.id, author=_host(db_session), body=f"comment {i}")
        seqs.append(comment.comment_seq)
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == 3
