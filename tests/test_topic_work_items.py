"""Tests for unified topic work items."""

from __future__ import annotations


def _create_topic(client, headers, project, **overrides):
    payload = {"title": "work-items", "description": "d"}
    payload.update(overrides)
    resp = client.post(f"/api/v1/projects/{project['id']}/topics", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _obligation_work_items_by_kind(progress: dict, kind: str) -> list[dict]:
    items: list[dict] = []
    for topic_item in progress.get("items", []):
        for work_item in topic_item.get("work_items", []):
            if work_item.get("kind") == kind and work_item.get("priority") == "obligation":
                items.append(work_item)
    return items


def test_todos_obligation_matches_work_items_projection(client, auth_headers, project, reviewer):
    topic = _create_topic(client, auth_headers, project, title="equiv")
    tid = topic["id"]
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=auth_headers,
        json={"body": "host opens"},
    )
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=reviewer["headers"],
        json={"body": "participant says hi"},
    )

    todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    progress = client.get("/api/v1/agents/me/topic-progress", headers=auth_headers).json()

    reply_todos = todos["pending_topic_replies"]
    assert len(reply_todos) == 1
    work_items = progress["items"][0]["work_items"]
    obligation_replies = [w for w in work_items if w["kind"] == "pending_topic_reply"]
    assert len(obligation_replies) == 1
    assert obligation_replies[0]["idempotency_key"] == f"pending_topic_reply:{tid}:{reply_todos[0]['comment_id']}"


def test_todos_round_ack_matches_work_items_projection(client, auth_headers, project, reviewer):
    topic = _create_topic(client, auth_headers, project, title="round-ack-equiv")
    tid = topic["id"]
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=reviewer["headers"],
        json={"body": "participant round1"},
    )
    summary = client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=auth_headers,
        json={"body": "## Round 1 Summary\n\n### 已共识\n- x\n"},
    )
    assert summary.status_code == 201, summary.text
    summary_id = summary.json()["id"]

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer["headers"]).json()
    progress = client.get("/api/v1/agents/me/topic-progress", headers=reviewer["headers"]).json()

    ack_todos = todos["pending_round_acks"]
    assert len(ack_todos) == 1
    assert ack_todos[0]["topic_id"] == tid
    assert ack_todos[0]["summary_comment_id"] == summary_id

    round_ack_items = _obligation_work_items_by_kind(progress, "round_ack")
    assert len(round_ack_items) == 1
    assert round_ack_items[0]["idempotency_key"] == f"round_ack:{tid}:{summary_id}"


def test_todos_mention_matches_work_items_projection(client, auth_headers, project, reviewer):
    topic = _create_topic(client, auth_headers, project, title="mention-equiv")
    tid = topic["id"]
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=auth_headers,
        json={"body": "@reviewer-agent 请看一下"},
    )

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer["headers"]).json()
    progress = client.get("/api/v1/agents/me/topic-progress", headers=reviewer["headers"]).json()

    topic_mentions = [m for m in todos["mentions"] if m["topic_id"] == tid]
    assert len(topic_mentions) == 1

    mention_items = _obligation_work_items_by_kind(progress, "mention")
    assert len(mention_items) == 1
    assert mention_items[0]["idempotency_key"] == f"mention:{topic_mentions[0]['id']}"


def test_obligation_and_contextual_work_items_same_topic(client, auth_headers, project, reviewer):
    topic = _create_topic(client, auth_headers, project, title="combo-items")
    tid = topic["id"]
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=auth_headers,
        json={"body": "host opens"},
    )
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=reviewer["headers"],
        json={"body": "participant first"},
    )

    todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    progress = client.get("/api/v1/agents/me/topic-progress", headers=auth_headers).json()

    assert len(todos["pending_topic_replies"]) == 1
    assert progress["total"] == 1
    work_items = progress["items"][0]["work_items"]
    by_kind = {(w["kind"], w["priority"]) for w in work_items}
    assert ("pending_topic_reply", "obligation") in by_kind
    assert ("unread_change", "contextual") in by_kind
    assert len(todos["pending_round_acks"]) == 0


def test_mention_three_views_consistent_cold_start(client, auth_headers, reviewer, project):
    topic = _create_topic(client, auth_headers, project, title="mention-3view")
    tid = topic["id"]
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=auth_headers,
        json={"body": "@reviewer-agent 请参与"},
    )

    reviewer_headers = reviewer["headers"]
    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    notifs = client.get("/api/v1/agents/me/notifications", headers=reviewer_headers).json()
    progress = client.get("/api/v1/agents/me/topic-progress", headers=reviewer_headers).json()

    topic_mentions = [m for m in todos["mentions"] if m["topic_id"] == tid]
    mentioned_notifs = [n for n in notifs["items"] if n["event"] == "agent.mentioned"]
    mention_work = _obligation_work_items_by_kind(progress, "mention")

    assert len(topic_mentions) == 1
    assert len(mentioned_notifs) >= 1
    assert len(mention_work) == 1
    assert mention_work[0]["idempotency_key"] == f"mention:{topic_mentions[0]['id']}"


def test_mention_three_views_consistent_after_prior_participation(
    client, auth_headers, reviewer, project
):
    topic = _create_topic(client, auth_headers, project, title="mention-3view-prior")
    tid = topic["id"]
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=reviewer["headers"],
        json={"body": "reviewer participated first"},
    )
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=auth_headers,
        json={"body": "@reviewer-agent 新一轮请你再看"},
    )

    reviewer_headers = reviewer["headers"]
    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    notifs = client.get("/api/v1/agents/me/notifications", headers=reviewer_headers).json()
    progress = client.get("/api/v1/agents/me/topic-progress", headers=reviewer_headers).json()

    topic_mentions = [m for m in todos["mentions"] if m["topic_id"] == tid]
    mentioned_notifs = [n for n in notifs["items"] if n["event"] == "agent.mentioned"]
    mention_work = _obligation_work_items_by_kind(progress, "mention")

    assert len(mentioned_notifs) >= 1, "notification should record agent.mentioned"
    assert len(topic_mentions) == 1, "todos.mentions should mirror notification"
    assert len(mention_work) == 1, "topic-progress should expose mention work item"
    assert mention_work[0]["idempotency_key"] == f"mention:{topic_mentions[0]['id']}"


def test_reviewer_cold_start_progress_empty(client, auth_headers, project, reviewer):
    topic = _create_topic(client, auth_headers, project, title="reviewer-filter")
    tid = topic["id"]
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=auth_headers,
        json={"body": "host only"},
    )

    progress = client.get("/api/v1/agents/me/topic-progress", headers=reviewer["headers"]).json()
    assert progress["total"] == 0


def test_dismiss_hides_topic_from_progress_and_todos(client, auth_headers, project, reviewer):
    topic = _create_topic(client, auth_headers, project, title="dismiss-progress")
    tid = topic["id"]
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=reviewer["headers"],
        json={"body": "needs host reply"},
    )

    before = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    assert len(before["pending_topic_replies"]) == 1

    dismissed = client.post(f"/api/v1/topics/{tid}/dismiss", headers=auth_headers)
    assert dismissed.status_code == 200

    todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    progress = client.get("/api/v1/agents/me/topic-progress", headers=auth_headers).json()
    assert todos["pending_topic_replies"] == []
    assert progress["total"] == 0
