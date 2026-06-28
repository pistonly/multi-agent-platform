def _create_topic(client, headers, project, **overrides):
    payload = {"title": "讨论：换不换方案", "description": "要不要切到新 pipeline"}
    payload.update(overrides)
    resp = client.post(f"/api/v1/projects/{project['id']}/topics", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_topic_crud(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project)
    assert topic["status"] == "open"
    assert topic["comment_count"] == 0
    assert topic["experiment_count"] == 0

    listing = client.get(f"/api/v1/projects/{project['id']}/topics", headers=auth_headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    detail = client.get(f"/api/v1/topics/{topic['id']}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["experiments"] == []

    updated = client.patch(f"/api/v1/topics/{topic['id']}", headers=auth_headers, json={"title": "已更新"})
    assert updated.status_code == 200
    assert updated.json()["title"] == "已更新"

    deleted = client.delete(f"/api/v1/topics/{topic['id']}", headers=auth_headers)
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/topics/{topic['id']}", headers=auth_headers).status_code == 404


def test_topic_status_filter_and_transitions(client, auth_headers, project):
    t1 = _create_topic(client, auth_headers, project)
    _create_topic(client, auth_headers, project, title="第二个")

    opened = client.get(f"/api/v1/projects/{project['id']}/topics?status=open", headers=auth_headers)
    assert len(opened.json()) == 2

    closed = client.post(f"/api/v1/topics/{t1['id']}/close", headers=auth_headers)
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"

    closed_only = client.get(
        f"/api/v1/projects/{project['id']}/topics?status=closed", headers=auth_headers
    )
    assert len(closed_only.json()) == 1

    reopened = client.post(f"/api/v1/topics/{t1['id']}/reopen", headers=auth_headers)
    assert reopened.json()["status"] == "open"


def test_topic_comments_tree(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project)
    parent = client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "顶层评论"},
    )
    assert parent.status_code == 201
    parent_id = parent.json()["id"]

    child = client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "回复", "parent_id": parent_id},
    )
    assert child.status_code == 201

    tree = client.get(f"/api/v1/topics/{topic['id']}/comments?tree=true", headers=auth_headers)
    assert tree.status_code == 200
    nodes = tree.json()
    assert len(nodes) == 1
    assert nodes[0]["body"] == "顶层评论"
    assert len(nodes[0]["children"]) == 1
    assert nodes[0]["children"][0]["body"] == "回复"

    detail = client.get(f"/api/v1/topics/{topic['id']}", headers=auth_headers)
    assert detail.json()["comment_count"] == 2


def test_experiment_linked_to_topic(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project)

    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "正式实验",
            "plan": {"content_md": "plan"},
            "submit_for_review": False,
            "topic_id": topic["id"],
        },
    )
    assert exp.status_code == 201
    assert exp.json()["topic_id"] == topic["id"]
    exp_id = exp.json()["id"]

    exp_detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers)
    assert exp_detail.status_code == 200
    assert exp_detail.json()["topic_id"] == topic["id"]

    bundle = client.get(f"/api/v1/experiments/{exp_id}/bundle", headers=auth_headers)
    assert bundle.status_code == 200
    assert bundle.json()["experiment"]["topic_id"] == topic["id"]

    detail = client.get(f"/api/v1/topics/{topic['id']}", headers=auth_headers)
    assert detail.json()["experiment_count"] == 1
    assert detail.json()["experiments"][0]["id"] == exp.json()["id"]


def test_topic_cross_project_isolation(client, auth_headers, project, admin_headers):
    other = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={"project_key": "other-proj", "name": "Other", "workspace_path": "/tmp/other"},
    )
    assert other.status_code == 201
    other_topic = client.post(
        f"/api/v1/projects/{other.json()['id']}/topics",
        headers=admin_headers,
        json={"title": "别的项目话题"},
    ).json()

    # 普通代理访问别的项目的话题 → 403
    forbidden = client.get(f"/api/v1/topics/{other_topic['id']}", headers=auth_headers)
    assert forbidden.status_code == 403

    # 在本项目建实验，topic_id 指向别的项目 → 404
    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "x", "plan": {"content_md": "p"}, "topic_id": other_topic["id"]},
    )
    assert resp.status_code == 404


def test_project_status_includes_open_topics(client, auth_headers, project):
    open_topic = _create_topic(client, auth_headers, project, title="进行中的讨论")
    closed_topic = _create_topic(client, auth_headers, project, title="已关闭的讨论")
    client.post(f"/api/v1/topics/{closed_topic['id']}/close", headers=auth_headers)

    status = client.get(f"/api/v1/projects/{project['id']}/status", headers=auth_headers)
    assert status.status_code == 200
    body = status.json()
    assert "open_topics" in body
    open_ids = {t["id"] for t in body["open_topics"]}
    assert open_topic["id"] in open_ids
    assert closed_topic["id"] not in open_ids
    match = next(t for t in body["open_topics"] if t["id"] == open_topic["id"])
    assert match["title"] == "进行中的讨论"
    assert match["status"] == "open"
    assert match["creator_name"] == "test-agent"


def test_topic_shows_creator_and_comment_author_names(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project, title="作者展示测试")
    assert topic["creator_name"] == "test-agent"

    comment = client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "一条评论"},
    )
    assert comment.status_code == 201
    assert comment.json()["author_name"] == "test-agent"

    detail = client.get(f"/api/v1/topics/{topic['id']}", headers=auth_headers)
    assert detail.json()["creator_name"] == "test-agent"
    assert detail.json()["comments"][0]["author_name"] == "test-agent"


def test_cannot_create_second_active_experiment_on_topic(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project)
    first = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "唯一活跃实验", "plan": {"content_md": "p"}, "topic_id": topic["id"]},
    )
    assert first.status_code == 201

    second = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "重复实验", "plan": {"content_md": "p2"}, "topic_id": topic["id"]},
    )
    assert second.status_code == 409

    client.post(f"/api/v1/experiments/{first.json()['id']}/cancel", headers=auth_headers)
    third = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "取消后可再建", "plan": {"content_md": "p3"}, "topic_id": topic["id"]},
    )
    assert third.status_code == 201


def test_cannot_create_experiment_on_closed_topic(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project)
    closed = client.post(f"/api/v1/topics/{topic['id']}/close", headers=auth_headers)
    assert closed.status_code == 200

    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "关闭后实验", "plan": {"content_md": "p"}, "topic_id": topic["id"]},
    )
    assert resp.status_code == 409
    assert "closed" in resp.json()["detail"].lower()


def test_only_topic_host_can_create_experiment_from_topic(client, auth_headers, reviewer, project):
    topic = _create_topic(client, auth_headers, project)

    denied = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=reviewer["headers"],
        json={"title": "非主持抢开", "plan": {"content_md": "p"}, "topic_id": topic["id"]},
    )
    assert denied.status_code == 403
    assert "host" in denied.json()["detail"].lower()

    allowed = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "主持开实验", "plan": {"content_md": "p"}, "topic_id": topic["id"]},
    )
    assert allowed.status_code == 201


def test_admin_can_create_experiment_from_others_topic(client, admin_headers, auth_headers, project):
    topic = _create_topic(client, auth_headers, project, title="他人主持的话题")

    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=admin_headers,
        json={"title": "管理员代开", "plan": {"content_md": "p"}, "topic_id": topic["id"]},
    )
    assert resp.status_code == 201
    assert resp.json()["topic_id"] == topic["id"]
    assert resp.json()["creator_agent_id"] != topic["creator_agent_id"]


def test_topic_archive_hidden_by_default(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project)
    archived = client.patch(
        f"/api/v1/topics/{topic['id']}",
        headers=auth_headers,
        json={"archived": True},
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None

    listing = client.get(f"/api/v1/projects/{project['id']}/topics", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json() == []

    with_archived = client.get(
        f"/api/v1/projects/{project['id']}/topics?include_archived=true",
        headers=auth_headers,
    )
    assert len(with_archived.json()) == 1

    restored = client.patch(
        f"/api/v1/topics/{topic['id']}",
        headers=auth_headers,
        json={"archived": False},
    )
    assert restored.json()["archived_at"] is None


def test_experiment_archive_allows_new_active_on_topic(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project, title="归档后重开实验")
    first = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "第一个", "plan": {"content_md": "p"}, "topic_id": topic["id"]},
    )
    assert first.status_code == 201
    exp_id = first.json()["id"]

    archived = client.patch(
        f"/api/v1/experiments/{exp_id}",
        headers=auth_headers,
        json={"archived": True},
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None

    second = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "第二个", "plan": {"content_md": "p2"}, "topic_id": topic["id"]},
    )
    assert second.status_code == 201, second.text
