"""P0: same-second host thread reply clears pending_topic_reply via comment_seq order."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from server.domain.models import TopicComment


def test_same_second_host_reply_clears_pending_topic_reply(
    client, auth_headers, project, reviewer, db_session
):
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "same-second-host-reply", "description": "d"},
    ).json()
    tid = topic["id"]
    participant_headers = reviewer["headers"]

    participant_comment = client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=participant_headers,
        json={"body": "participant needs host reply"},
    ).json()
    host_reply = client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=auth_headers,
        json={
            "body": "host replies in thread",
            "parent_id": participant_comment["id"],
        },
    ).json()

    stamp = datetime(2026, 7, 5, 1, 0, 0, tzinfo=timezone.utc)
    ids = [uuid.UUID(participant_comment["id"]), uuid.UUID(host_reply["id"])]
    for row in db_session.scalars(select(TopicComment).where(TopicComment.id.in_(ids))):
        row.created_at = stamp
    db_session.commit()

    assert int(participant_comment["comment_seq"]) < int(host_reply["comment_seq"])

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
