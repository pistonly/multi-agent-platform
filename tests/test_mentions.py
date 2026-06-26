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
