"""T3: comment_seq cursor read API and obligation preservation."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow


def _create_topic(client, headers, project, **overrides):
    payload = {"title": "cursor-topic", "description": "d"}
    payload.update(overrides)
    resp = client.post(f"/api/v1/projects/{project['id']}/topics", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_topic_read_advances_cursor_and_clears_unread_progress(
    client, auth_headers, project, reviewer
):
    topic = _create_topic(client, auth_headers, project, title="read-cursor")
    tid = topic["id"]
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=auth_headers,
        json={"body": "host opens"},
    )
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=reviewer["headers"],
        json={"body": "reviewer says hi"},
    )

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


def test_topic_read_does_not_clear_mention_obligation(client, auth_headers, reviewer, project):
    """Cursor read must not dismiss mention obligation (T3-2)."""
    topic = _create_topic(client, auth_headers, project, title="read-mention")
    tid = topic["id"]
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=auth_headers,
        json={"body": "@reviewer-agent please review"},
    )

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


def test_comment_seq_strictly_monotonic_per_topic(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project, title="seq-mono")
    tid = topic["id"]
    seqs = []
    for i in range(3):
        resp = client.post(
            f"/api/v1/topics/{tid}/comments",
            headers=auth_headers,
            json={"body": f"comment {i}"},
        )
        assert resp.status_code == 201, resp.text
        seqs.append(resp.json()["comment_seq"])
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == 3
