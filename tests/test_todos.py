def test_todos_aggregation(client, auth_headers, reviewer, project):
    # 发起人创建实验并提交评审
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "待办实验", "plan": {"content_md": "p"}, "submit_for_review": True},
    ).json()

    # 评审者应看到待评审
    reviewer_headers = reviewer["headers"]
    reviewer_todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert any(e["id"] == exp["id"] for e in reviewer_todos["pending_reviews"])

    # 发起者应看到自己的进行中实验
    agent_todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    assert any(e["id"] == exp["id"] for e in agent_todos["my_open_experiments"])
    creator_exp = next(e for e in agent_todos["my_open_experiments"] if e["id"] == exp["id"])
    assert creator_exp["open_unreasonable_count"] == 0

    # 评审后，pending_reviews 不再包含该实验
    client.post(
        f"/api/v1/experiments/{exp['id']}/reviews",
        headers=reviewer_headers,
        json={"unreasonable_items": ["需要补充验收标准"]},
    )
    reviewer_todos2 = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert not any(e["id"] == exp["id"] for e in reviewer_todos2["pending_reviews"])

    agent_todos_after_review = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    creator_exp_after_review = next(
        e for e in agent_todos_after_review["my_open_experiments"] if e["id"] == exp["id"]
    )
    assert creator_exp_after_review["open_unreasonable_count"] == 1

    # 发起者修订计划并标记该不合理项为「已修改」(addressed) → 双方都应看到待回复
    item = client.get(f"/api/v1/experiments/{exp['id']}/reviews", headers=reviewer_headers).json()[0]
    unreasonable = [i for i in item["items"] if i["kind"] == "unreasonable"][0]
    client.post(
        f"/api/v1/experiments/{exp['id']}/plans",
        headers=auth_headers,
        json={
            "content_md": "p v2",
            "change_note": "已处理不合理项",
            "addressed_item_ids": [unreasonable["id"]],
        },
    )

    agent_todos2 = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    assert any(r["item_id"] == unreasonable["id"] for r in agent_todos2["pending_replies"])
    reviewer_todos3 = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert any(r["item_id"] == unreasonable["id"] for r in reviewer_todos3["pending_replies"])

    # 发起者创建话题 → 出现在 my_open_topics
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "待办话题"},
    ).json()
    agent_todos3 = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    assert any(t["id"] == topic["id"] for t in agent_todos3["my_open_topics"])


def test_dismiss_topic_hides_from_my_open_topics(client, auth_headers, project):
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "Dismiss 一下"},
    ).json()

    # Sanity: it's there before dismiss.
    todos_before = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    assert any(t["id"] == topic["id"] for t in todos_before["my_open_topics"])

    resp = client.post(
        f"/api/v1/topics/{topic['id']}/dismiss", headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dismissed_at"] is not None

    todos_after = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    assert not any(t["id"] == topic["id"] for t in todos_after["my_open_topics"])

    # Idempotent
    resp2 = client.post(
        f"/api/v1/topics/{topic['id']}/dismiss", headers=auth_headers
    )
    assert resp2.status_code == 200
    assert resp2.json()["dismissed_at"] == body["dismissed_at"]


def test_new_topic_comment_resurrects_dismissed_topic(
    client, auth_headers, reviewer, project
):
    """A new comment on a dismissed topic bumps updated_at past dismissed_at,
    so the topic re-surfaces in the host's todos."""
    host_headers = auth_headers
    participant_headers = reviewer["headers"]

    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=host_headers,
        json={"title": "Resurrect 测试"},
    ).json()
    client.post(
        f"/api/v1/topics/{topic['id']}/dismiss", headers=host_headers
    )

    hidden = client.get("/api/v1/agents/me/todos", headers=host_headers).json()
    assert not any(t["id"] == topic["id"] for t in hidden["my_open_topics"])

    # New activity (any participant's comment) bumps topic.updated_at.
    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=participant_headers,
        json={"body": "有新动静了"},
    )

    resurface = client.get("/api/v1/agents/me/todos", headers=host_headers).json()
    matching = [t for t in resurface["my_open_topics"] if t["id"] == topic["id"]]
    assert len(matching) == 1
    assert matching[0]["dismissed_at"] is not None  # still flagged, but visible


def test_dismiss_topic_forbidden_for_non_creator(
    client, auth_headers, reviewer, project
):
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "非 host 不能 dismiss"},
    ).json()
    resp = client.post(
        f"/api/v1/topics/{topic['id']}/dismiss",
        headers=reviewer["headers"],
    )
    assert resp.status_code == 404


def test_pending_topic_replies(client, auth_headers, reviewer, project):
    host_headers = auth_headers
    participant_headers = reviewer["headers"]

    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=host_headers,
        json={"title": "主持待回复测试"},
    ).json()

    top = client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=participant_headers,
        json={"body": "顶层评论需要主持回复"},
    ).json()

    todos = client.get("/api/v1/agents/me/todos", headers=host_headers).json()
    assert len(todos["pending_topic_replies"]) == 1
    pending = todos["pending_topic_replies"][0]
    assert pending["topic_id"] == topic["id"]
    assert pending["comment_id"] == top["id"]
    assert pending["thread_root_id"] == top["id"]
    assert pending["topic_title"] == "主持待回复测试"

    child = client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=participant_headers,
        json={"body": "子评论也需要回复", "parent_id": top["id"]},
    ).json()

    todos2 = client.get("/api/v1/agents/me/todos", headers=host_headers).json()
    assert len(todos2["pending_topic_replies"]) == 2
    child_pending = next(p for p in todos2["pending_topic_replies"] if p["comment_id"] == child["id"])
    assert child_pending["thread_root_id"] == top["id"]

    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=host_headers,
        json={"body": "主持回复整 thread", "parent_id": top["id"]},
    )

    todos3 = client.get("/api/v1/agents/me/todos", headers=host_headers).json()
    assert todos3["pending_topic_replies"] == []

    client.post(f"/api/v1/topics/{topic['id']}/close", headers=host_headers)
    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=participant_headers,
        json={"body": "关闭后不应出现"},
    )
    client.post(f"/api/v1/topics/{topic['id']}/reopen", headers=host_headers)

    other_topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=participant_headers,
        json={"title": "他人主持话题"},
    ).json()
    client.post(
        f"/api/v1/topics/{other_topic['id']}/comments",
        headers=host_headers,
        json={"body": "主持在他人话题评论"},
    )
    todos4 = client.get("/api/v1/agents/me/todos", headers=host_headers).json()
    assert not any(p["topic_id"] == other_topic["id"] for p in todos4["pending_topic_replies"])


def test_thread_root_id_helper():
    from types import SimpleNamespace
    from uuid import uuid4

    from server.services.todo_service import thread_root_id

    a, b, c = uuid4(), uuid4(), uuid4()
    by_id = {
        a: SimpleNamespace(id=a, parent_comment_id=None),
        b: SimpleNamespace(id=b, parent_comment_id=a),
        c: SimpleNamespace(id=c, parent_comment_id=b),
    }
    assert thread_root_id(a, by_id) == a
    assert thread_root_id(b, by_id) == a
    assert thread_root_id(c, by_id) == a


def test_list_experiments_filter_search_pagination(client, auth_headers, project):
    for i in range(3):
        client.post(
            f"/api/v1/projects/{project['id']}/experiments",
            headers=auth_headers,
            json={"title": f"实验 {i}", "plan": {"content_md": "p"}, "submit_for_review": i == 0},
        )

    review_list = client.get(
        f"/api/v1/projects/{project['id']}/experiments?phase=review", headers=auth_headers
    )
    assert review_list.status_code == 200
    assert len(review_list.json()) == 1

    search = client.get(
        f"/api/v1/projects/{project['id']}/experiments?q=%E5%AE%9E%E9%AA%8C%201",
        headers=auth_headers,
    )
    assert len(search.json()) == 1

    paged = client.get(
        f"/api/v1/projects/{project['id']}/experiments?page=1&page_size=2", headers=auth_headers
    )
    assert len(paged.json()) == 2
    assert paged.headers["X-Total-Count"] == "3"


def test_list_topics_search_pagination(client, auth_headers, project):
    for i in range(3):
        client.post(
            f"/api/v1/projects/{project['id']}/topics",
            headers=auth_headers,
            json={"title": f"话题 {i}"},
        )

    closed = client.post(
        f"/api/v1/projects/{project['id']}/topics", headers=auth_headers, json={"title": "要关闭的"}
    ).json()
    client.post(f"/api/v1/topics/{closed['id']}/close", headers=auth_headers)

    open_list = client.get(
        f"/api/v1/projects/{project['id']}/topics?status=open", headers=auth_headers
    )
    assert len(open_list.json()) == 3

    search = client.get(
        f"/api/v1/projects/{project['id']}/topics?q=%E8%AF%9D%E9%A2%98%201", headers=auth_headers
    )
    assert len(search.json()) == 1

    paged = client.get(
        f"/api/v1/projects/{project['id']}/topics?page=1&page_size=2", headers=auth_headers
    )
    assert len(paged.json()) == 2
    assert paged.headers["X-Total-Count"] == "4"
