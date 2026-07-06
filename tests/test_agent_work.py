"""Tests for GET /agents/me/work unified snapshot."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow


def _create_topic(client, headers, project, **overrides):
    payload = {"title": "work-topic", "description": "d"}
    payload.update(overrides)
    resp = client.post(f"/api/v1/projects/{project['id']}/topics", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_agent_work_matches_split_endpoints(client, auth_headers, project, reviewer):
    topic = _create_topic(client, auth_headers, project, title="work-unified")
    tid = topic["id"]
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=auth_headers,
        json={"body": "host opens"},
    )
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=reviewer["headers"],
        json={"body": "participant reply"},
    )

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


def test_agent_work_notification_category_wakeable_default(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project, title="work-notif")
    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "ping"},
    )

    wakeable = client.get("/api/v1/agents/me/work", headers=auth_headers).json()
    all_notifs = client.get(
        "/api/v1/agents/me/work",
        headers=auth_headers,
        params={"notification_category": "all"},
    ).json()

    assert wakeable["notifications"]["total"] <= all_notifs["notifications"]["total"]


def test_agent_work_empty_when_host_caught_up(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project, title="work-caught-up")
    tid = topic["id"]
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=auth_headers,
        json={"body": "only me"},
    )

    work = client.get("/api/v1/agents/me/work", headers=auth_headers).json()
    assert work["topic_progress"]["total"] == 0
    assert work["todos"]["pending_topic_replies"] == []
