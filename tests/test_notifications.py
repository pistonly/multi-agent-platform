from tests._frontmatter import make_valid_plan


def test_notifications_on_phase_change(client, auth_headers, reviewer, project):
    reviewer_headers = reviewer["headers"]
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "Notify Test",
            "plan": {"content_md": make_valid_plan(body="# plan")},
        },
    ).json()

    client.post(f"/api/v1/experiments/{exp['id']}/submit-review", headers=auth_headers)

    # reviewer (map-admin style) should get notification
    res = client.get("/api/v1/agents/me/notifications", headers=reviewer_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["unread_count"] >= 1
    events = [n["event"] for n in data["items"]]
    assert "experiment.phase_changed" in events

    notif = next(n for n in data["items"] if n["event"] == "experiment.phase_changed")
    assert notif["read_at"] is None

    read_res = client.post(f"/api/v1/notifications/{notif['id']}/read", headers=reviewer_headers)
    assert read_res.status_code == 200
    assert read_res.json()["read_at"] is not None

    unread_res = client.get(
        "/api/v1/agents/me/notifications", headers=reviewer_headers, params={"unread_only": True}
    )
    unread_ids = {n["id"] for n in unread_res.json()["items"]}
    assert notif["id"] not in unread_ids


def test_mark_all_notifications_read(client, auth_headers, reviewer, project):
    reviewer_headers = reviewer["headers"]
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "Bulk read", "plan": {"content_md": make_valid_plan(body="# p")}},
    ).json()
    client.post(f"/api/v1/experiments/{exp['id']}/submit-review", headers=auth_headers)

    before = client.get("/api/v1/agents/me/notifications", headers=reviewer_headers).json()
    assert before["unread_count"] >= 1

    marked = client.post("/api/v1/agents/me/notifications/read-all", headers=reviewer_headers)
    assert marked.status_code == 200
    assert marked.json()["marked"] >= 1

    after = client.get("/api/v1/agents/me/notifications", headers=reviewer_headers).json()
    assert after["unread_count"] == 0


def test_actor_does_not_receive_own_notification(client, auth_headers, project):
    client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "Self notify", "plan": {"content_md": make_valid_plan(body="# p")}},
    )
    res = client.get("/api/v1/agents/me/notifications", headers=auth_headers)
    assert res.status_code == 200
    # creator should not be notified for their own experiment.created
    for item in res.json()["items"]:
        assert item["event"] != "experiment.created" or item["summary"] != "创建实验「Self notify」"


def test_notifications_default_to_digest_and_filter_by_category(client, auth_headers, reviewer, project):
    reviewer_headers = reviewer["headers"]
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "Category filter", "plan": {"content_md": make_valid_plan(body="# p")}},
    ).json()
    client.post(f"/api/v1/experiments/{exp['id']}/submit-review", headers=auth_headers)

    all_res = client.get("/api/v1/agents/me/notifications", headers=reviewer_headers)
    assert all_res.status_code == 200
    digest_items = [n for n in all_res.json()["items"] if n["event"] == "experiment.phase_changed"]
    assert digest_items
    assert digest_items[0]["category"] == "digest"
    assert digest_items[0]["wake_version"] == 1
    assert digest_items[0]["event_count"] >= 1

    digest_res = client.get(
        "/api/v1/agents/me/notifications",
        headers=reviewer_headers,
        params={"category": "digest", "unread_only": True},
    )
    assert any(n["event"] == "experiment.phase_changed" for n in digest_res.json()["items"])

    wakeable_res = client.get(
        "/api/v1/agents/me/notifications",
        headers=reviewer_headers,
        params={"category": "wakeable", "unread_only": True},
    )
    assert not any(n["event"] == "experiment.phase_changed" for n in wakeable_res.json()["items"])

    invalid = client.get(
        "/api/v1/agents/me/notifications",
        headers=reviewer_headers,
        params={"category": "urgent"},
    )
    assert invalid.status_code == 422


def test_digest_notifications_are_aggregated_by_object(client, auth_headers, reviewer, project):
    reviewer_headers = reviewer["headers"]
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "Aggregate digest", "plan": {"content_md": make_valid_plan(body="# p")}},
    ).json()

    client.post(f"/api/v1/experiments/{exp['id']}/submit-review", headers=auth_headers)
    client.post(f"/api/v1/experiments/{exp['id']}/withdraw", headers=auth_headers)
    client.post(f"/api/v1/experiments/{exp['id']}/submit-review", headers=auth_headers)

    data = client.get(
        "/api/v1/agents/me/notifications",
        headers=reviewer_headers,
        params={"category": "digest"},
    ).json()
    rows = [
        n
        for n in data["items"]
        if n["event"] == "experiment.phase_changed" and n["target_id"] == exp["id"]
    ]
    assert len(rows) == 1
    assert rows[0]["event_count"] == 2
    assert rows[0]["category"] == "digest"


def test_dispatch_notification_agent_to_agent(client, auth_headers, reviewer, project):
    """5a50c841 A1: POST /agents/me/notifications/dispatch delivers a wakeable
    notification to another agent in the same project."""
    sender_id = client.get("/api/v1/agents/me", headers=auth_headers).json()["id"]
    reviewer_id = client.get("/api/v1/agents/me", headers=reviewer["headers"]).json()["id"]
    assert sender_id != reviewer_id

    res = client.post(
        "/api/v1/agents/me/notifications/dispatch",
        headers=auth_headers,
        json={
            "recipient_agent_id": reviewer_id,
            "event": "host.invoke.cancelled",
            "summary": "host invoke 等待超过 5s 已取消;target session state=waiting-for-session",
            "target_type": "experiment",
            "wakeable": True,
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["recipient_agent_id"] == reviewer_id
    assert body["event"] == "host.invoke.cancelled"
    assert body["category"] == "wakeable"

    recv = client.get(
        "/api/v1/agents/me/notifications",
        headers=reviewer["headers"],
        params={"unread_only": True, "category": "wakeable"},
    ).json()
    assert recv["items"][0]["event"] == "host.invoke.cancelled"
    assert "已取消" in recv["items"][0]["summary"]


def test_dispatch_notification_self_rejected(client, auth_headers, project):
    """Dispatch to self is a no-op for the service (actor excluded) → 400."""
    sender_id = client.get("/api/v1/agents/me", headers=auth_headers).json()["id"]
    res = client.post(
        "/api/v1/agents/me/notifications/dispatch",
        headers=auth_headers,
        json={
            "recipient_agent_id": sender_id,
            "event": "host.invoke.cancelled",
            "summary": "self dispatch should fail",
        },
    )
    assert res.status_code == 400


def test_dispatch_notification_unknown_recipient(client, auth_headers, project):
    """Unknown recipient agent → 404."""
    import uuid as uuid_lib

    res = client.post(
        "/api/v1/agents/me/notifications/dispatch",
        headers=auth_headers,
        json={
            "recipient_agent_id": str(uuid_lib.uuid4()),
            "event": "host.invoke.cancelled",
            "summary": "nobody home",
        },
    )
    assert res.status_code == 404
