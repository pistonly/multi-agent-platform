def test_notifications_on_phase_change(client, auth_headers, reviewer, project):
    reviewer_headers = reviewer["headers"]
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "Notify Test",
            "plan": {"content_md": "# plan"},
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
        json={"title": "Bulk read", "plan": {"content_md": "# p"}},
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
        json={"title": "Self notify", "plan": {"content_md": "# p"}},
    )
    res = client.get("/api/v1/agents/me/notifications", headers=auth_headers)
    assert res.status_code == 200
    # creator should not be notified for their own experiment.created
    for item in res.json()["items"]:
        assert item["event"] != "experiment.created" or item["summary"] != f"创建实验「Self notify」"
