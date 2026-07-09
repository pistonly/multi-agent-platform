import uuid
from datetime import UTC, datetime, timedelta

import pytest

from server.domain.models import Topic
from tests._frontmatter import make_valid_plan

pytestmark = pytest.mark.slow


def test_todos_aggregation(client, auth_headers, reviewer, project):
    # 发起人创建实验并提交评审
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "待办实验", "plan": {"content_md": make_valid_plan(body="p")}, "submit_for_review": True},
    ).json()

    # 评审者应看到待评审
    reviewer_headers = reviewer["headers"]
    reviewer_todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert any(e["id"] == exp["id"] for e in reviewer_todos["pending_reviews"])

    # 发起者应看到自己的进行中实验，但不应出现在 pending_reviews（评审是 reviewer 义务）
    agent_todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    assert not any(e["id"] == exp["id"] for e in agent_todos["pending_reviews"])
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
    assert len(agent_todos_after_review["pending_plan_revisions"]) == 1
    plan_rev = agent_todos_after_review["pending_plan_revisions"][0]
    assert plan_rev["experiment_id"] == exp["id"]
    assert plan_rev["open_unreasonable_count"] == 1
    assert plan_rev["blocked_on"] == "open_unreasonable_item"

    # 发起者修订计划并标记该不合理项为「已修改」(addressed)。
    # plan revise 会 auto-archive 旧 plan review；旧 item 进入历史记录，
    # 不再作为双方的 live pending_replies。reviewer 通过 v2 pending_reviews
    # 继续评审当前 plan version。
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
    assert agent_todos2["pending_plan_revisions"] == []
    assert agent_todos2["pending_replies"] == []
    creator_exp_v2 = next(e for e in agent_todos2["my_open_experiments"] if e["id"] == exp["id"])
    assert creator_exp_v2["open_unreasonable_count"] == 0
    assert creator_exp_v2["actions"] == []
    assert creator_exp_v2["blocked_on"] == "awaiting_review_for_current_plan_version"

    reviewer_todos3 = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert reviewer_todos3["pending_replies"] == []
    # 修订计划后应重新出现在 pending_reviews（按 current_plan_version 判定）
    pending_v2 = next(e for e in reviewer_todos3["pending_reviews"] if e["id"] == exp["id"])
    assert pending_v2["current_plan_version"] == 2

    # 发起者创建话题 → 出现在 my_open_topics
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "待办话题"},
    ).json()
    agent_todos3 = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    assert any(t["id"] == topic["id"] for t in agent_todos3["my_open_topics"])


def test_pending_result_reviews(client, auth_headers, reviewer, project):
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "结果审批实验", "plan": {"content_md": make_valid_plan(body="p")}, "submit_for_review": True},
    ).json()
    exp_id = exp["id"]
    client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["OK"]},
    )
    client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    client.post(f"/api/v1/experiments/{exp_id}/start", headers=auth_headers)

    running_todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    running_exp = next(e for e in running_todos["my_open_experiments"] if e["id"] == exp_id)
    assert running_exp["phase"] == "running"
    assert running_exp["log_count"] == 0
    assert running_exp["latest_log_summary"] is None

    client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=auth_headers,
        json={
            "summary": "提交结果",
            "content_md": "结果内容",
            "metadata": {"pytest_summary": "unit passed"},
        },
    )

    creator_todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    assert any(e["id"] == exp_id for e in creator_todos["my_open_experiments"])
    assert not any(e["id"] == exp_id for e in creator_todos["pending_result_reviews"])

    reviewer_todos = client.get("/api/v1/agents/me/todos", headers=reviewer["headers"]).json()
    assert any(e["id"] == exp_id for e in reviewer_todos["pending_result_reviews"])


def test_archived_addressed_review_items_do_not_block_review_progress(
    client, auth_headers, reviewer, project
):
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "archived addressed item should not block",
            "plan": {"content_md": make_valid_plan(body="p v1")},
            "submit_for_review": True,
        },
    ).json()
    exp_id = exp["id"]

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"unreasonable_items": ["needs detail"]},
    ).json()
    unreasonable_id = next(
        item["id"] for item in review["items"] if item["kind"] == "unreasonable"
    )

    revise = client.post(
        f"/api/v1/experiments/{exp_id}/plans",
        headers=auth_headers,
        json={
            "content_md": make_valid_plan(body="p v2 with detail"),
            "change_note": "addressed old review",
            "addressed_item_ids": [unreasonable_id],
        },
    )
    assert revise.status_code == 201, revise.text

    # The old v1 review is now archived. Its addressed items are historical
    # state, not live reviewer/host work.
    host_todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    reviewer_todos = client.get("/api/v1/agents/me/todos", headers=reviewer["headers"]).json()
    assert host_todos["pending_replies"] == []
    assert reviewer_todos["pending_replies"] == []

    pending_v2 = next(e for e in reviewer_todos["pending_reviews"] if e["id"] == exp_id)
    assert pending_v2["actions"] == ["review_add"]

    clean_review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["v2 looks good"]},
    )
    assert clean_review.status_code == 201, clean_review.text

    host_todos_after_review = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    host_exp = next(e for e in host_todos_after_review["my_open_experiments"] if e["id"] == exp_id)
    assert host_exp["open_unreasonable_count"] == 0
    assert host_exp["actions"] == ["approve", "withdraw"]
    assert host_todos_after_review["pending_replies"] == []

    reviewer_todos_after_review = client.get(
        "/api/v1/agents/me/todos", headers=reviewer["headers"]
    ).json()
    assert reviewer_todos_after_review["pending_replies"] == []

    approve = client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    assert approve.status_code == 200, approve.text
    assert approve.json()["phase"] == "approved"


def test_archived_experiments_are_excluded_from_my_open_experiments(
    client, auth_headers, project
):
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "归档实验不进待办", "plan": {"content_md": make_valid_plan(body="p")}},
    ).json()

    before = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    assert any(e["id"] == exp["id"] for e in before["my_open_experiments"])

    archived = client.patch(
        f"/api/v1/experiments/{exp['id']}",
        headers=auth_headers,
        json={"archived": True},
    )
    assert archived.status_code == 200, archived.text

    after = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    assert not any(e["id"] == exp["id"] for e in after["my_open_experiments"])


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


def test_stale_open_topics_surface_after_threshold(
    client, auth_headers, project, db_session
):
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "久未推进的话题"},
    ).json()
    stale_at = datetime.now(UTC) - timedelta(minutes=31)
    row = db_session.get(Topic, uuid.UUID(topic["id"]))
    row.updated_at = stale_at
    db_session.commit()

    todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()

    stale = [t for t in todos["stale_open_topics"] if t["topic_id"] == topic["id"]]
    assert len(stale) == 1
    assert stale[0]["topic_title"] == "久未推进的话题"
    assert stale[0]["stale_since"] is not None


def test_stale_open_topics_threshold_is_parameterized(
    client, auth_headers, project, db_session
):
    """f873c287 I1(c): ``list_stale_open_topics`` accepts an explicit
    ``threshold_minutes`` override. Same DB state, different threshold,
    deterministic difference. We hit the HTTP layer to keep timezone
    roundtripping consistent with the existing surface-after-threshold
    test.
    """
    # Tight window (1 minute): 5-min-old topic is stale.
    topic_tight = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "tight window"},
    ).json()
    row = db_session.get(Topic, uuid.UUID(topic_tight["id"]))
    row.updated_at = datetime.now(UTC) - timedelta(minutes=5)
    db_session.commit()

    from server.domain.models import Agent
    from server.services.todo_service import list_stale_open_topics

    agent_obj = db_session.get(Agent, row.creator_agent_id)
    db_session.expire_all()
    stale_tight = list_stale_open_topics(
        db_session, agent_obj, threshold_minutes=1
    )
    assert any(str(t.topic_id) == topic_tight["id"] for t in stale_tight)

    # Generous window (60 minutes): same 5-min-old topic is NOT stale.
    db_session.expire_all()
    stale_loose = list_stale_open_topics(
        db_session, agent_obj, threshold_minutes=60
    )
    assert not any(str(t.topic_id) == topic_tight["id"] for t in stale_loose)


def test_stale_open_topics_threshold_via_settings_env(
    client, auth_headers, project, db_session, monkeypatch
):
    """f873c287 I1(c): ``MAP_STALE_OPEN_TOPIC_THRESHOLD_MINUTES`` env var
    flows through ``Settings`` into ``list_stale_open_topics``. We force
    a 1-minute threshold via env, then assert a 5-min-old topic shows up
    in ``/agents/me/todos`` without any explicit threshold argument (i.e.
    caller used the default settings-based path).
    """
    from server.config import get_settings

    monkeypatch.setenv("MAP_STALE_OPEN_TOPIC_THRESHOLD_MINUTES", "1")
    get_settings.cache_clear()
    try:
        topic = client.post(
            f"/api/v1/projects/{project['id']}/topics",
            headers=auth_headers,
            json={"title": "settings env 派生话题"},
        ).json()
        row = db_session.get(Topic, uuid.UUID(topic["id"]))
        row.updated_at = datetime.now(UTC) - timedelta(minutes=5)
        db_session.commit()

        todos = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
        assert any(t["topic_id"] == topic["id"] for t in todos["stale_open_topics"])
    finally:
        # Reset the cache so other tests aren't pinned to the 1-minute override.
        get_settings.cache_clear()


def test_stale_open_topics_respects_dismiss_and_stronger_obligations(
    client, auth_headers, reviewer, project, db_session
):
    host_headers = auth_headers
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=host_headers,
        json={"title": "待回复优先"},
    ).json()
    stale_at = datetime.now(UTC) - timedelta(minutes=31)
    row = db_session.get(Topic, uuid.UUID(topic["id"]))
    row.updated_at = stale_at
    db_session.commit()

    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=reviewer["headers"],
        json={"body": "先回复这个"},
    )
    row = db_session.get(Topic, uuid.UUID(topic["id"]))
    row.updated_at = stale_at
    db_session.commit()

    with_reply = client.get("/api/v1/agents/me/todos", headers=host_headers).json()
    assert any(p["topic_id"] == topic["id"] for p in with_reply["pending_topic_replies"])
    assert not any(t["topic_id"] == topic["id"] for t in with_reply["stale_open_topics"])

    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=host_headers,
        json={"body": "已回复"},
    )
    row = db_session.get(Topic, uuid.UUID(topic["id"]))
    row.updated_at = stale_at
    db_session.commit()

    client.post(f"/api/v1/topics/{topic['id']}/dismiss", headers=host_headers)
    dismissed = client.get("/api/v1/agents/me/todos", headers=host_headers).json()
    assert not any(t["topic_id"] == topic["id"] for t in dismissed["stale_open_topics"])


def test_pending_plan_revisions_three_states(client, auth_headers, reviewer, project):
    """AC#6: review+open unreasonable / review+0 / approved partition correctly."""
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "plan-rev states", "plan": {"content_md": make_valid_plan(body="p")}, "submit_for_review": True},
    ).json()
    exp_id = exp["id"]
    reviewer_headers = reviewer["headers"]

    # review + 0 open unreasonable → no pending_plan_revisions
    client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer_headers,
        json={"reasonable_items": ["OK"]},
    )
    todos_ok = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    assert todos_ok["pending_plan_revisions"] == []

    # add unreasonable on new plan version path: revoke via new review after revise
    item = client.get(f"/api/v1/experiments/{exp_id}/reviews", headers=reviewer_headers).json()[0]
    unreasonable = [i for i in item["items"] if i["kind"] == "unreasonable"]
    if not unreasonable:
        review2 = client.post(
            f"/api/v1/experiments/{exp_id}/reviews",
            headers=reviewer_headers,
            json={"unreasonable_items": ["gap"]},
        )
        assert review2.status_code == 409  # already reviewed v1

    # withdraw reviewer review and re-submit with unreasonable
    review_id = item["id"]
    client.post(
        f"/api/v1/experiments/{exp_id}/reviews/{review_id}/withdraw",
        headers=reviewer_headers,
    )
    client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer_headers,
        json={"unreasonable_items": ["needs revise"]},
    )
    todos_open = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    assert len(todos_open["pending_plan_revisions"]) == 1

    # approve → no pending_plan_revisions (not in review)
    item2 = client.get(f"/api/v1/experiments/{exp_id}/reviews", headers=reviewer_headers).json()[0]
    unres = [i for i in item2["items"] if i["kind"] == "unreasonable"][0]
    client.post(
        f"/api/v1/experiments/{exp_id}/plans",
        headers=auth_headers,
        json={
            "content_md": "p v2",
            "addressed_item_ids": [unres["id"]],
        },
    )
    client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer_headers,
        json={"reasonable_items": ["fixed"]},
    )
    client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    todos_approved = client.get("/api/v1/agents/me/todos", headers=auth_headers).json()
    assert todos_approved["pending_plan_revisions"] == []


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


def test_pending_topic_replies_host_opens_participant_replies_in_thread(
    client, auth_headers, reviewer, project
):
    """Host opening comment must not satisfy reply obligation for later participant replies."""
    host_headers = auth_headers
    participant_headers = reviewer["headers"]

    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=host_headers,
        json={"title": "主持开场后楼中楼待回复"},
    ).json()
    opening = client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=host_headers,
        json={"body": "host Round 1 开场"},
    ).json()
    participant_reply = client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=participant_headers,
        json={"body": "participant 跟评", "parent_id": opening["id"]},
    ).json()

    todos = client.get("/api/v1/agents/me/todos", headers=host_headers).json()
    assert len(todos["pending_topic_replies"]) == 1
    assert todos["pending_topic_replies"][0]["comment_id"] == participant_reply["id"]

    progress = client.get("/api/v1/agents/me/topic-progress", headers=host_headers).json()
    assert progress["total"] == 1
    work_items = progress["items"][0].get("work_items") or []
    assert any(w["kind"] == "pending_topic_reply" for w in work_items)


def test_thread_root_id_helper():
    from types import SimpleNamespace
    from uuid import uuid4

    from server.services.thread_activity import thread_root_id

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
            json={"title": f"实验 {i}", "plan": {"content_md": make_valid_plan(body="p")}, "submit_for_review": i == 0},
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


def test_no_write_on_repeated_todos(client, auth_headers, reviewer, project, db_session):
    """Repeated GET /todos must not UPDATE mentions (T1 A/E)."""
    import uuid

    from sqlalchemy import select

    from server.domain.models import Mention

    reviewer_headers = reviewer["headers"]
    reviewer_id = uuid.UUID(reviewer["id"])
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "Read idempotent", "description": "d"},
    ).json()
    root = client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "@reviewer-agent ping"},
    ).json()
    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=reviewer_headers,
        json={"body": "replied", "parent_id": root["id"]},
    )
    mention = db_session.scalar(
        select(Mention).where(
            Mention.mentioned_agent_id == reviewer_id,
            Mention.source_id == uuid.UUID(root["id"]),
        )
    )
    assert mention is not None
    mention.dismissed_at = None
    db_session.commit()

    for _ in range(5):
        client.get("/api/v1/agents/me/todos", headers=reviewer_headers)

    db_session.refresh(mention)
    assert mention.dismissed_at is None


def test_mention_dismiss_on_comment_write_path(client, auth_headers, reviewer, project, db_session):
    """Posting a comment dismisses stale mentions on the write path (T1 B)."""
    import uuid

    from sqlalchemy import select

    from server.domain.models import Mention

    reviewer_headers = reviewer["headers"]
    reviewer_id = uuid.UUID(reviewer["id"])
    topic = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=auth_headers,
        json={"title": "Write dismiss", "description": "d"},
    ).json()
    root = client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "@reviewer-agent legacy open row"},
    ).json()
    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=reviewer_headers,
        json={"body": "first reply", "parent_id": root["id"]},
    )
    mention = db_session.scalar(
        select(Mention).where(
            Mention.mentioned_agent_id == reviewer_id,
            Mention.source_id == uuid.UUID(root["id"]),
        )
    )
    assert mention is not None
    mention.dismissed_at = None
    db_session.commit()

    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=reviewer_headers,
        json={"body": "nudge write-path dismiss"},
    )
    db_session.refresh(mention)
    assert mention.dismissed_at is not None


def test_todos_persona_filter_review_partitions(
    client, auth_headers, reviewer, project, admin_headers
):
    """Host/participant personas omit review obligation buckets; reviewer keeps them."""
    from server.services.notification_service import PERSONA_AGENT_NAMES

    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "persona-filter-exp", "plan": {"content_md": make_valid_plan(body="p")}, "submit_for_review": True},
    ).json()
    exp_id = exp["id"]

    host_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        params={
            "name": PERSONA_AGENT_NAMES["host"],
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert host_resp.status_code == 201
    host_headers = {"Authorization": f"Bearer {host_resp.json()['api_token']}"}

    participant_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        params={
            "name": PERSONA_AGENT_NAMES["participant"],
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert participant_resp.status_code == 201
    participant_headers = {
        "Authorization": f"Bearer {participant_resp.json()['api_token']}"
    }

    reviewer_todos = client.get("/api/v1/agents/me/todos", headers=reviewer["headers"]).json()
    assert any(e["id"] == exp_id for e in reviewer_todos["pending_reviews"])
    assert reviewer_todos.get("experiment_review_informational") == []

    host_todos = client.get("/api/v1/agents/me/todos", headers=host_headers).json()
    assert host_todos["pending_reviews"] == []
    assert host_todos["pending_result_reviews"] == []
    host_info = host_todos["experiment_review_informational"]
    assert any(i["experiment_title"] == "persona-filter-exp" for i in host_info)
    assert host_info[0]["review_progress"] == "0/1"

    participant_todos = client.get(
        "/api/v1/agents/me/todos", headers=participant_headers
    ).json()
    assert participant_todos["pending_reviews"] == []
    assert participant_todos["pending_result_reviews"] == []
    assert any(
        i["experiment_title"] == "persona-filter-exp"
        for i in participant_todos["experiment_review_informational"]
    )


def test_todos_persona_filter_admin_sees_full_partitions(
    client, auth_headers, reviewer, project, admin_headers
):
    """Admin keeps review obligation buckets; informational stays empty."""
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "admin-filter-exp", "plan": {"content_md": make_valid_plan(body="p")}, "submit_for_review": True},
    ).json()
    exp_id = exp["id"]

    admin_todos = client.get("/api/v1/agents/me/todos", headers=admin_headers).json()
    assert any(e["id"] == exp_id for e in admin_todos["pending_reviews"])
    assert admin_todos.get("experiment_review_informational") == []

    admin_work = client.get(
        "/api/v1/agents/me/todos",
        headers=admin_headers,
        params={"include_all_partitions": True},
    ).json()
    assert any(e["id"] == exp_id for e in admin_work["pending_reviews"])
