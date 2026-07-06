import pytest

pytestmark = pytest.mark.slow


def test_mention_in_experiment_comment_creates_todo_and_notification(
    client, auth_headers, reviewer, project
):
    reviewer_headers = reviewer["headers"]
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "Mention exp", "plan": {"content_md": "# p"}},
    ).json()
    plan = client.get(f"/api/v1/experiments/{exp['id']}/plans/1", headers=auth_headers).json()

    client.post(
        f"/api/v1/experiments/{exp['id']}/comments",
        headers=auth_headers,
        json={
            "anchor_type": "plan",
            "anchor_id": plan["id"],
            "body": "请 @reviewer-agent 看一下这个计划",
        },
    )

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert len(todos["mentions"]) >= 1
    mention = todos["mentions"][0]
    assert mention["experiment_id"] == exp["id"]
    assert "reviewer-agent" in mention["excerpt"] or "看一下" in mention["excerpt"]

    notifs = client.get("/api/v1/agents/me/notifications", headers=reviewer_headers).json()
    assert any(n["event"] == "agent.mentioned" for n in notifs["items"])


def test_mention_in_topic_comment(client, auth_headers, reviewer, project):
    reviewer_headers = reviewer["headers"]
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "Mention topic", "description": "d"},
    ).json()

    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "@reviewer-agent 请参与讨论"},
    )

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert any(m["topic_id"] == topic["id"] for m in todos["mentions"])


def test_self_mention_ignored(client, auth_headers, project):
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "Self", "plan": {"content_md": "# p"}},
    ).json()
    plan = client.get(f"/api/v1/experiments/{exp['id']}/plans/1", headers=auth_headers).json()
    me = client.get("/api/v1/agents/me", headers=auth_headers).json()

    client.post(
        f"/api/v1/experiments/{exp['id']}/comments",
        headers=auth_headers,
        json={
            "anchor_type": "plan",
            "anchor_id": plan["id"],
            "body": f"@{me['name']} 自言自语",
        },
    )

    todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    assert not any(m["author_agent_id"] == me["id"] for m in todos["mentions"])


def test_unknown_mention_name_soft_warns_author(client, auth_headers, reviewer, project):
    reviewer_headers = reviewer["headers"]
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "Unknown", "plan": {"content_md": "# p"}},
    ).json()
    plan = client.get(f"/api/v1/experiments/{exp['id']}/plans/1", headers=auth_headers).json()

    resp = client.post(
        f"/api/v1/experiments/{exp['id']}/comments",
        headers=auth_headers,
        json={
            "anchor_type": "plan",
            "anchor_id": plan["id"],
            "body": "@no-such-agent hello",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["unresolved_mentions"] == ["no-such-agent"]

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert todos["mentions"] == []

    notifs = client.get("/api/v1/agents/me/notifications", headers=auth_headers).json()
    assert any(n["event"] == "mention.unresolved" for n in notifs["items"])


def test_unknown_topic_mention_soft_warns_author(client, auth_headers, project):
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "Bad mention", "description": "d"},
    ).json()

    resp = client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "@reviewer please join"},
    )
    assert resp.status_code == 201
    assert resp.json()["unresolved_mentions"] == ["reviewer"]

    notifs = client.get("/api/v1/agents/me/notifications", headers=auth_headers).json()
    unresolved = [n for n in notifs["items"] if n["event"] == "mention.unresolved"]
    assert unresolved
    assert unresolved[0]["payload_json"]["unresolved_mentions"] == ["reviewer"]


def test_valid_mention_has_empty_unresolved(client, auth_headers, reviewer, project):
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "Good mention", "description": "d"},
    ).json()

    resp = client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "@reviewer-agent please join"},
    )
    assert resp.status_code == 201
    assert resp.json()["unresolved_mentions"] == []


def test_dismiss_single_mention(client, auth_headers, reviewer, project):
    reviewer_headers = reviewer["headers"]
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "Dismiss one", "plan": {"content_md": "# p"}},
    ).json()
    plan = client.get(f"/api/v1/experiments/{exp['id']}/plans/1", headers=auth_headers).json()

    client.post(
        f"/api/v1/experiments/{exp['id']}/comments",
        headers=auth_headers,
        json={
            "anchor_type": "plan",
            "anchor_id": plan["id"],
            "body": "@reviewer-agent 看一眼",
        },
    )

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert len(todos["mentions"]) == 1
    mention_id = todos["mentions"][0]["id"]
    assert todos["mentions"][0]["dismissed_at"] is None

    resp = client.post(
        f"/api/v1/agents/me/mentions/{mention_id}/dismiss",
        headers=reviewer_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == mention_id
    assert body["dismissed_at"] is not None

    todos_after = client.get(
        "/api/v1/agents/me/todos", headers=reviewer_headers
    ).json()
    assert todos_after["mentions"] == []

    # idempotent: dismissing twice still 200
    resp2 = client.post(
        f"/api/v1/agents/me/mentions/{mention_id}/dismiss",
        headers=reviewer_headers,
    )
    assert resp2.status_code == 200
    assert resp2.json()["dismissed_at"] == body["dismissed_at"]


def test_dismiss_all_mentions(client, auth_headers, reviewer, project):
    reviewer_headers = reviewer["headers"]
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "Dismiss all", "description": "d"},
    ).json()
    for body in [
        "@reviewer-agent first",
        "@reviewer-agent second",
        "no mention here",
    ]:
        client.post(
            f"/api/v1/topics/{topic['id']}/comments",
            headers=auth_headers,
            json={"body": body},
        )

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert len(todos["mentions"]) == 2

    resp = client.post(
        "/api/v1/agents/me/mentions/dismiss-all", headers=reviewer_headers
    )
    assert resp.status_code == 200
    assert resp.json()["dismissed"] == 2

    todos_after = client.get(
        "/api/v1/agents/me/todos", headers=reviewer_headers
    ).json()
    assert todos_after["mentions"] == []


def test_dismiss_other_agents_mention_forbidden(
    client, auth_headers, reviewer, project
):
    """A mention addressed to someone else must not be dismissable by me."""
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "Forbidden", "plan": {"content_md": "# p"}},
    ).json()
    plan = client.get(f"/api/v1/experiments/{exp['id']}/plans/1", headers=auth_headers).json()

    client.post(
        f"/api/v1/experiments/{exp['id']}/comments",
        headers=auth_headers,
        json={
            "anchor_type": "plan",
            "anchor_id": plan["id"],
            "body": "@reviewer-agent ping",
        },
    )

    # `auth_headers` belongs to a different agent; trying to dismiss
    # reviewer's mention should 404 (we hide existence behind not-found).
    todos = client.get("/api/v1/agents/me/todos", headers=reviewer["headers"]).json()
    assert len(todos["mentions"]) == 1
    mention_id = todos["mentions"][0]["id"]

    resp = client.post(
        f"/api/v1/agents/me/mentions/{mention_id}/dismiss",
        headers=auth_headers,
    )
    assert resp.status_code == 404

    # And reviewer can still see it untouched
    todos_after = client.get(
        "/api/v1/agents/me/todos", headers=reviewer["headers"]
    ).json()
    assert len(todos_after["mentions"]) == 1
    assert todos_after["mentions"][0]["dismissed_at"] is None


def test_auto_dismiss_on_reply_in_topic_thread(client, auth_headers, reviewer, project):
    """When the mentioned agent posts a reply in the same topic thread,
    any prior @-mentions of him in that thread auto-dismiss."""
    reviewer_headers = reviewer["headers"]
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "Auto dismiss", "description": "d"},
    ).json()

    # Root comment by auth user that @-mentions reviewer.
    root = client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "Round 1 Summary @reviewer-agent 请看"},
    ).json()

    # Reviewer replies to the thread — should auto-dismiss the prior @mention.
    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=reviewer_headers,
        json={"body": "已读", "parent_id": root["id"]},
    )

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert todos["mentions"] == [], (
        "reviewer's mention should auto-dismiss after he replied in the thread"
    )


def test_auto_dismiss_does_not_touch_other_topic(
    client, auth_headers, reviewer, project
):
    """Container auto-dismiss is per topic — replying on topic A must not clear topic B."""
    reviewer_headers = reviewer["headers"]
    topic_a = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "Topic A", "description": "d"},
    ).json()
    topic_b = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "Topic B", "description": "d"},
    ).json()

    client.post(
        f"/api/v1/topics/{topic_a['id']}/comments",
        headers=auth_headers,
        json={"body": "Topic A @reviewer-agent 看 a"},
    )
    client.post(
        f"/api/v1/topics/{topic_b['id']}/comments",
        headers=auth_headers,
        json={"body": "Topic B @reviewer-agent 看 b"},
    )

    client.post(
        f"/api/v1/topics/{topic_a['id']}/comments",
        headers=reviewer_headers,
        json={"body": "回复 A"},
    )

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert len(todos["mentions"]) == 1
    assert todos["mentions"][0]["topic_id"] == topic_b["id"]


def test_auto_dismiss_topic_on_top_level_comment(
    client, auth_headers, reviewer, project
):
    """Top-level reply after @mentions dismisses them via participation rules (T1 D2)."""
    reviewer_headers = reviewer["headers"]
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "Top-level reply", "description": "d"},
    ).json()

    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "Thread A @reviewer-agent 看 a"},
    )
    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "Thread B @reviewer-agent 看 b"},
    )

    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=reviewer_headers,
        json={"body": "新顶层回复，未挂 parent"},
    )

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert todos["mentions"] == []


def test_stale_mention_filtered_in_todos_without_read_write(
    client, auth_headers, reviewer, project, db_session
):
    """get_todos must not write; stale mentions are filtered in projection only."""
    import uuid

    from sqlalchemy import select

    from server.domain.models import Mention

    reviewer_headers = reviewer["headers"]
    reviewer_id = uuid.UUID(reviewer["id"])
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "Stale projection", "description": "d"},
    ).json()
    root = client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "@reviewer-agent stale mention"},
    ).json()
    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=reviewer_headers,
        json={"body": "already replied", "parent_id": root["id"]},
    )

    mention = db_session.scalar(
        select(Mention).where(
            Mention.mentioned_agent_id == reviewer_id,
            Mention.source_id == uuid.UUID(root["id"]),
        )
    )
    assert mention is not None
    assert mention.dismissed_at is not None

    mention.dismissed_at = None
    db_session.commit()

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert todos["mentions"] == []
    db_session.refresh(mention)
    assert mention.dismissed_at is None


def test_mention_read_notification_does_not_clear_obligation(
    client, auth_headers, reviewer, project
):
    """Reading agent.mentioned notification must not dismiss mention obligation (T2 PR2 c)."""
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "read-notif-only", "description": "d"},
    ).json()
    tid = topic["id"]
    client.post(
        f"/api/v1/topics/{tid}/comments",
        headers=auth_headers,
        json={"body": "@reviewer-agent please review"},
    )

    reviewer_headers = reviewer["headers"]
    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert len(todos["mentions"]) == 1
    progress_before = client.get(
        "/api/v1/agents/me/topic-progress", headers=reviewer_headers
    ).json()
    mention_work_before = [
        w
        for item in progress_before.get("items", [])
        for w in item.get("work_items", [])
        if w.get("kind") == "mention" and w.get("priority") == "obligation"
    ]
    assert len(mention_work_before) == 1

    notifs = client.get(
        "/api/v1/agents/me/notifications",
        headers=reviewer_headers,
        params={"unread_only": True},
    ).json()
    mentioned = [
        n for n in notifs["items"] if n["event"] == "agent.mentioned" and n.get("read_at") is None
    ]
    assert len(mentioned) >= 1
    notif_id = mentioned[0]["id"]

    read_resp = client.post(
        f"/api/v1/notifications/{notif_id}/read",
        headers=reviewer_headers,
    )
    assert read_resp.status_code == 200

    todos_after = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert len(todos_after["mentions"]) == 1
    progress_after = client.get(
        "/api/v1/agents/me/topic-progress", headers=reviewer_headers
    ).json()
    mention_work_after = [
        w
        for item in progress_after.get("items", [])
        for w in item.get("work_items", [])
        if w.get("kind") == "mention" and w.get("priority") == "obligation"
    ]
    assert len(mention_work_after) == 1


def test_dismiss_mention_cascades_notification_read(
    client, auth_headers, reviewer, project
):
    reviewer_headers = reviewer["headers"]
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "dismiss-cascade", "description": "d"},
    ).json()
    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "@reviewer-agent cascade test"},
    )
    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    mention_id = todos["mentions"][0]["id"]

    notifs_before = client.get(
        "/api/v1/agents/me/notifications",
        headers=reviewer_headers,
        params={"unread_only": True, "limit": 50},
    ).json()
    assert any(n["event"] == "agent.mentioned" for n in notifs_before["items"])

    client.post(
        f"/api/v1/agents/me/mentions/{mention_id}/dismiss",
        headers=reviewer_headers,
    )

    todos_after = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert todos_after["mentions"] == []
    notifs_after = client.get(
        "/api/v1/agents/me/notifications",
        headers=reviewer_headers,
        params={"unread_only": True, "limit": 50},
    ).json()
    assert not any(
        n["event"] == "agent.mentioned" and n.get("read_at") is None
        for n in notifs_after["items"]
    )
