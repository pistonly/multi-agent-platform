def test_topic_comment_notifies_host_and_broadcasts(
    client, auth_headers, reviewer, project, agent_token, admin_headers
):
    participant_headers = reviewer["headers"]

    third = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        params={"name": "third-agent", "role": "agent", "project_key": project["project_key"]},
    )
    assert third.status_code == 201
    third_headers = {"Authorization": f"Bearer {third.json()['api_token']}"}

    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "通知测试话题"},
    ).json()

    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=participant_headers,
        json={"body": "participant 顶层评论"},
    ).json()

    host_notifs = client.get("/api/v1/agents/me/notifications", headers=auth_headers).json()
    host_topic_notifs = [
        n for n in host_notifs["items"] if n["event"] == "topic.comment.created"
    ]
    assert len(host_topic_notifs) == 1
    assert host_topic_notifs[0]["summary"] == "【主持】话题新评论待回复"
    assert host_topic_notifs[0]["payload_json"]["host_directed"] is True

    participant_notifs = client.get(
        "/api/v1/agents/me/notifications", headers=participant_headers
    ).json()
    participant_topic_notifs = [
        n for n in participant_notifs["items"] if n["event"] == "topic.comment.created"
    ]
    assert len(participant_topic_notifs) == 0

    third_notifs = client.get("/api/v1/agents/me/notifications", headers=third_headers).json()
    broadcast = [n for n in third_notifs["items"] if n["event"] == "topic.comment.created"]
    assert len(broadcast) == 1
    assert broadcast[0]["summary"] == "话题新评论"
    assert "host_directed" not in (broadcast[0].get("payload_json") or {})


def test_host_own_comment_no_self_notification(client, auth_headers, project):
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "自评论话题"},
    ).json()

    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "主持自己评论"},
    )

    host_notifs = client.get("/api/v1/agents/me/notifications", headers=auth_headers).json()
    topic_events = [n for n in host_notifs["items"] if n["event"] == "topic.comment.created"]
    assert len(topic_events) == 0


def test_create_experiment_no_topic_id_warning(client, auth_headers, project):
    client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "open 话题"},
    )

    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "无话题实验", "plan": {"content_md": "p"}},
    ).json()
    assert exp["warnings"] == ["no_topic_id"]


def test_create_experiment_with_topic_id_no_warning(client, auth_headers, project):
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "绑定话题"},
    ).json()

    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "有话题实验",
            "plan": {"content_md": "p"},
            "topic_id": topic["id"],
        },
    ).json()
    assert exp["warnings"] == []
