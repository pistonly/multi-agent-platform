"""Tests for topic progress API."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow


def _create_topic(client, headers, project, **overrides):
    payload = {"title": "progress-topic", "description": "d"}
    payload.update(overrides)
    resp = client.post(f"/api/v1/projects/{project['id']}/topics", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_topic_progress_empty_when_last_comment_is_mine(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project, title="progress-a")
    tid = topic["id"]
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=auth_headers,
        json={"body": "host opens"},
    )
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=auth_headers,
        json={"body": "host again last"},
    )
    progress = client.get("/api/v1/agents/me/topic-progress", headers=auth_headers).json()
    assert progress["total"] == 0
    assert progress["items"] == []


def test_topic_progress_returns_comments_after_cursor(client, auth_headers, project, reviewer):
    topic = _create_topic(client, auth_headers, project, title="progress-b")
    tid = topic["id"]
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=auth_headers,
        json={"body": "host opens"},
    )
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=reviewer["headers"],
        json={"body": "reviewer reply"},
    )
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=auth_headers,
        json={"body": "host follow-up"},
    )
    client.post(
        f"/api/v1/agents/me/topics/{tid}/read",
        headers=auth_headers,
    )
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=reviewer["headers"],
        json={"body": "reviewer newest"},
    )

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


def test_topic_progress_empty_when_never_participated(client, auth_headers, project, reviewer):
    topic = _create_topic(client, auth_headers, project, title="progress-c")
    tid = topic["id"]
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=auth_headers,
        json={"body": "only host so far"},
    )

    progress = client.get("/api/v1/agents/me/topic-progress", headers=reviewer["headers"]).json()
    assert progress["total"] == 0
