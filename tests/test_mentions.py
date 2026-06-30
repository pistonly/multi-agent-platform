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


def test_unknown_mention_name_ignored(client, auth_headers, reviewer, project):
    reviewer_headers = reviewer["headers"]
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "Unknown", "plan": {"content_md": "# p"}},
    ).json()
    plan = client.get(f"/api/v1/experiments/{exp['id']}/plans/1", headers=auth_headers).json()

    client.post(
        f"/api/v1/experiments/{exp['id']}/comments",
        headers=auth_headers,
        json={
            "anchor_type": "plan",
            "anchor_id": plan["id"],
            "body": "@no-such-agent hello",
        },
    )

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert todos["mentions"] == []


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


def test_auto_dismiss_does_not_touch_other_thread(
    client, auth_headers, reviewer, project
):
    """Auto-dismiss only clears mentions inside the same thread, not others."""
    reviewer_headers = reviewer["headers"]
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "Cross-thread", "description": "d"},
    ).json()

    root_a = client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "Thread A @reviewer-agent 看 a"},
    ).json()
    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "Thread B @reviewer-agent 看 b"},
    )

    # Reviewer replies only to thread A.
    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=reviewer_headers,
        json={"body": "只看 a", "parent_id": root_a["id"]},
    )

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    # Thread A's mention auto-dismissed; Thread B's remains.
    assert len(todos["mentions"]) == 1
    assert todos["mentions"][0]["excerpt"].startswith("Thread B")
