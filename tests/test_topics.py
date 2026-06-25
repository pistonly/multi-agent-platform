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
