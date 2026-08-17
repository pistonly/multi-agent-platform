"""Tests for unified topic work items (M58b-3: DB-direct fixtures).

Every assertion here targets a retained consumer projection — todos ↔
topic-progress ↔ notifications consistency — so fixtures are inserted via
the DB factory instead of the retired HTTP write endpoints. Round-ack
fixtures go through ``record_participant_round_ack`` (the same service
function the retired advance-round endpoint called), which keeps the
system-kind ack comment shape authentic.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from server.domain.models import Agent, Mention
from server.services import mention_service, topic_lifecycle_service
from tests._db_topic_factory import db_add_comment, db_create_topic

pytestmark = pytest.mark.slow


def _flatten_comments(nodes: list[dict]) -> list[dict]:
    out: list[dict] = []
    for node in nodes:
        out.append(node)
        out.extend(_flatten_comments(node.get("children") or []))
    return out


def _agents(db_session, reviewer):
    host = db_session.scalar(select(Agent).where(Agent.name == "test-agent"))
    rev = db_session.get(Agent, uuid.UUID(reviewer["id"]))
    return host, rev


def _topic(db_session, project, host, title):
    topic = db_create_topic(
        db_session,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=host.id,
        title=title,
        description="d",
    )
    return topic


def _obligation_work_items_by_kind(progress: dict, kind: str) -> list[dict]:
    items: list[dict] = []
    for topic_item in progress.get("items", []):
        for work_item in topic_item.get("work_items", []):
            if work_item.get("kind") == kind and work_item.get("priority") == "obligation":
                items.append(work_item)
    return items


def test_todos_obligation_matches_work_items_projection(
    client, auth_headers, project, reviewer, db_session
):
    host, rev = _agents(db_session, reviewer)
    topic = _topic(db_session, project, host, "equiv")
    tid = str(topic.id)
    db_add_comment(db_session, topic_id=topic.id, author=host, body="host opens")
    db_add_comment(db_session, topic_id=topic.id, author=rev, body="participant says hi")

    todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    progress = client.get("/api/v1/agents/me/topic-progress", headers=auth_headers).json()

    reply_todos = todos["pending_topic_replies"]
    assert len(reply_todos) == 1
    work_items = progress["items"][0]["work_items"]
    obligation_replies = [w for w in work_items if w["kind"] == "pending_topic_reply"]
    assert len(obligation_replies) == 1
    assert obligation_replies[0]["idempotency_key"] == f"pending_topic_reply:{tid}:{reply_todos[0]['comment_id']}"


def test_todos_round_ack_matches_work_items_projection(
    client, auth_headers, project, reviewer, db_session
):
    host, rev = _agents(db_session, reviewer)
    topic = _topic(db_session, project, host, "round-ack-equiv")
    tid = str(topic.id)
    db_add_comment(db_session, topic_id=topic.id, author=rev, body="participant round1")
    summary = db_add_comment(
        db_session,
        topic_id=topic.id,
        author=host,
        body="## Round 1 Summary\n\n### 已共识\n- x\n",
    )
    summary_id = str(summary.id)

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer["headers"]).json()
    progress = client.get("/api/v1/agents/me/topic-progress", headers=reviewer["headers"]).json()

    ack_todos = todos["pending_round_acks"]
    assert len(ack_todos) == 1
    assert ack_todos[0]["topic_id"] == tid
    assert ack_todos[0]["summary_comment_id"] == summary_id

    round_ack_items = _obligation_work_items_by_kind(progress, "round_ack")
    assert len(round_ack_items) == 1
    assert round_ack_items[0]["idempotency_key"] == f"round_ack:{tid}:{summary_id}"


def test_todos_mention_matches_work_items_projection(
    client, auth_headers, project, reviewer, db_session
):
    host, _rev = _agents(db_session, reviewer)
    topic = _topic(db_session, project, host, "mention-equiv")
    tid = str(topic.id)
    db_add_comment(
        db_session, topic_id=topic.id, author=host, body="@reviewer-agent 请看一下"
    )

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer["headers"]).json()
    progress = client.get("/api/v1/agents/me/topic-progress", headers=reviewer["headers"]).json()

    topic_mentions = [m for m in todos["mentions"] if m["topic_id"] == tid]
    assert len(topic_mentions) == 1

    mention_items = _obligation_work_items_by_kind(progress, "mention")
    assert len(mention_items) == 1
    assert mention_items[0]["idempotency_key"] == f"mention:{topic_mentions[0]['id']}"


def test_obligation_and_contextual_work_items_same_topic(
    client, auth_headers, project, reviewer, db_session
):
    host, rev = _agents(db_session, reviewer)
    topic = _topic(db_session, project, host, "combo-items")
    db_add_comment(db_session, topic_id=topic.id, author=host, body="host opens")
    db_add_comment(db_session, topic_id=topic.id, author=rev, body="participant first")

    todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    progress = client.get("/api/v1/agents/me/topic-progress", headers=auth_headers).json()

    assert len(todos["pending_topic_replies"]) == 1
    assert progress["total"] == 1
    work_items = progress["items"][0]["work_items"]
    by_kind = {(w["kind"], w["priority"]) for w in work_items}
    assert ("pending_topic_reply", "obligation") in by_kind
    assert ("unread_change", "contextual") in by_kind
    assert len(todos["pending_round_acks"]) == 0


def test_system_ack_comment_skips_unread_change_work_item(
    client, auth_headers, project, reviewer, db_session
):
    """Ack comments are kind=system and must not create unread_change noise."""
    host, rev = _agents(db_session, reviewer)
    topic = _topic(db_session, project, host, "system-ack-unread")
    tid = str(topic.id)
    db_add_comment(db_session, topic_id=topic.id, author=rev, body="participant round1")
    db_add_comment(
        db_session,
        topic_id=topic.id,
        author=host,
        body="## Round 1 Summary\n\n### 已共识\n- x\n",
    )
    topic_lifecycle_service.record_participant_round_ack(db_session, topic.id, rev, "accept")

    detail = client.get(f"/api/v1/topics/{tid}", headers=auth_headers).json()
    ack_comments = [
        c
        for c in _flatten_comments(detail["comments"])
        if "map:ack=" in c.get("body", "")
    ]
    assert len(ack_comments) == 1
    assert ack_comments[0]["kind"] == "system"

    host_progress = client.get("/api/v1/agents/me/topic-progress", headers=auth_headers).json()
    unread = [
        w
        for item in host_progress.get("items", [])
        for w in item.get("work_items", [])
        if w.get("kind") == "unread_change"
    ]
    assert not any("map:ack=" in (w.get("excerpt") or "") for w in unread)


def test_system_ack_comment_skips_pending_topic_reply_work_item(
    client, auth_headers, project, reviewer, db_session
):
    """Ack comments are kind=system protocol signals, not conversational
    threads the host must reply to (feedback 002e2a4f / 58193bec). They
    must NOT create a pending_topic_reply obligation — the host already
    drives the round via round_ack / pending_advance_rounds items."""
    host, rev = _agents(db_session, reviewer)
    topic = _topic(db_session, project, host, "system-ack-reply")
    tid = str(topic.id)
    db_add_comment(db_session, topic_id=topic.id, author=rev, body="participant round1")
    db_add_comment(
        db_session,
        topic_id=topic.id,
        author=host,
        body="## Round 1 Summary\n\n### 已共识\n- x\n",
    )
    topic_lifecycle_service.record_participant_round_ack(db_session, topic.id, rev, "accept")

    detail = client.get(f"/api/v1/topics/{tid}", headers=auth_headers).json()
    ack_comments = [
        c
        for c in _flatten_comments(detail["comments"])
        if "map:ack=" in c.get("body", "")
    ]
    assert len(ack_comments) == 1
    assert ack_comments[0]["kind"] == "system"

    todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    reply_comment_ids = {r["comment_id"] for r in todos["pending_topic_replies"]}
    # The ack system comment must not be a pending reply for the host.
    assert ack_comments[0]["id"] not in reply_comment_ids


def test_mention_three_views_consistent_cold_start(
    client, auth_headers, reviewer, project, db_session
):
    host, _rev = _agents(db_session, reviewer)
    topic = _topic(db_session, project, host, "mention-3view")
    tid = str(topic.id)
    db_add_comment(
        db_session, topic_id=topic.id, author=host, body="@reviewer-agent 请参与"
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
    client, auth_headers, reviewer, project, db_session
):
    host, rev = _agents(db_session, reviewer)
    topic = _topic(db_session, project, host, "mention-3view-prior")
    tid = str(topic.id)
    db_add_comment(db_session, topic_id=topic.id, author=rev, body="reviewer participated first")
    db_add_comment(
        db_session, topic_id=topic.id, author=host, body="@reviewer-agent 新一轮请你再看"
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


def test_reviewer_cold_start_progress_empty(
    client, auth_headers, project, reviewer, db_session
):
    host, _rev = _agents(db_session, reviewer)
    topic = _topic(db_session, project, host, "reviewer-filter")
    db_add_comment(db_session, topic_id=topic.id, author=host, body="host only")

    progress = client.get("/api/v1/agents/me/topic-progress", headers=reviewer["headers"]).json()
    assert progress["total"] == 0


def test_dismiss_hides_topic_from_progress_and_todos(
    client, auth_headers, project, reviewer, db_session
):
    host, rev = _agents(db_session, reviewer)
    topic = _topic(db_session, project, host, "dismiss-progress")
    tid = str(topic.id)
    db_add_comment(db_session, topic_id=topic.id, author=rev, body="needs host reply")

    before = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    assert len(before["pending_topic_replies"]) == 1

    dismissed = client.post(f"/api/v1/topics/{tid}/dismiss", headers=auth_headers)
    assert dismissed.status_code == 200

    todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    progress = client.get("/api/v1/agents/me/topic-progress", headers=auth_headers).json()
    assert todos["pending_topic_replies"] == []
    assert progress["total"] == 0


def test_stale_mention_projection_matches_replied_after_mention(
    client, auth_headers, reviewer, project, db_session
):
    """Stale mention projection matches agent_replied_after_mention (T1 H)."""
    host, rev = _agents(db_session, reviewer)
    reviewer_id = rev.id
    topic = _topic(db_session, project, host, "stale-equiv")
    root = db_add_comment(
        db_session, topic_id=topic.id, author=host, body="@reviewer-agent check"
    )
    db_add_comment(
        db_session, topic_id=topic.id, author=rev, body="ok", parent_id=root.id
    )
    mention = db_session.scalar(
        select(Mention).where(
            Mention.mentioned_agent_id == reviewer_id,
            Mention.source_id == root.id,
        )
    )
    assert mention is not None
    mention.dismissed_at = None
    db_session.commit()

    stale = mention_service.agent_replied_after_mention(
        db_session, mention=mention, agent_id=reviewer_id
    )
    assert stale is True

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer["headers"]).json()
    progress = client.get("/api/v1/agents/me/topic-progress", headers=reviewer["headers"]).json()
    assert todos["mentions"] == []
    assert _obligation_work_items_by_kind(progress, "mention") == []
