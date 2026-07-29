import uuid

import pytest
from sqlalchemy import select

from server.domain.models import TopicComment
from tests._frontmatter import make_valid_plan

pytestmark = pytest.mark.slow


def _create_topic(client, headers, project, **overrides):
    payload = {"title": "讨论：换不换方案", "description": "要不要切到新 pipeline"}
    payload.update(overrides)
    resp = client.post(f"/api/v1/projects/{project['id']}/topics", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_topic_crud(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project)
    assert topic["status"] == "open"
    assert topic["discussion_round"] == "round1"
    assert topic["round_summary_count"] == 0
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


def test_topic_advance_round_state_machine(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project)

    # round1 → round2 → round3 → round4 (flexible, no upper bound)
    first = client.post(f"/api/v1/topics/{topic['id']}/advance-round", headers=auth_headers)
    assert first.status_code == 200
    assert first.json()["discussion_round"] == "round2"
    assert first.json()["round_summary_count"] == 1

    second = client.post(f"/api/v1/topics/{topic['id']}/advance-round", headers=auth_headers)
    assert second.status_code == 200
    assert second.json()["discussion_round"] == "round3"
    assert second.json()["round_summary_count"] == 2

    third = client.post(f"/api/v1/topics/{topic['id']}/advance-round", headers=auth_headers)
    assert third.status_code == 200
    assert third.json()["discussion_round"] == "round4"
    assert third.json()["round_summary_count"] == 3

    # mark_ready from any round
    ready = client.post(
        f"/api/v1/topics/{topic['id']}/advance-round",
        headers=auth_headers,
        json={"mark_ready": True},
    )
    assert ready.status_code == 200
    assert ready.json()["discussion_round"] == "ready"
    assert ready.json()["round_summary_count"] == 4

    done = client.post(f"/api/v1/topics/{topic['id']}/advance-round", headers=auth_headers)
    assert done.status_code == 409


def test_mark_ready_requires_at_least_one_summary(client, auth_headers, project):
    """mark_ready with 0 summaries and increment_summary=False must 409."""
    topic = _create_topic(client, auth_headers, project)

    too_early = client.post(
        f"/api/v1/topics/{topic['id']}/advance-round",
        headers=auth_headers,
        json={"mark_ready": True, "increment_summary": False},
    )
    assert too_early.status_code == 409


def test_only_topic_host_or_admin_can_advance_round(client, auth_headers, reviewer, admin_headers, project):
    topic = _create_topic(client, auth_headers, project)

    denied = client.post(f"/api/v1/topics/{topic['id']}/advance-round", headers=reviewer["headers"])
    assert denied.status_code == 403

    allowed = client.post(f"/api/v1/topics/{topic['id']}/advance-round", headers=admin_headers)
    assert allowed.status_code == 200
    assert allowed.json()["discussion_round"] == "round2"


def test_only_topic_host_or_admin_can_manage_topic(client, auth_headers, reviewer, admin_headers, project):
    topic = _create_topic(client, auth_headers, project)

    denied_patch = client.patch(
        f"/api/v1/topics/{topic['id']}",
        headers=reviewer["headers"],
        json={"pinned": True},
    )
    assert denied_patch.status_code == 403

    denied_close = client.post(f"/api/v1/topics/{topic['id']}/close", headers=reviewer["headers"])
    assert denied_close.status_code == 403

    admin_close = client.post(f"/api/v1/topics/{topic['id']}/close", headers=admin_headers)
    assert admin_close.status_code == 200
    assert admin_close.json()["status"] == "closed"

    denied_reopen = client.post(f"/api/v1/topics/{topic['id']}/reopen", headers=reviewer["headers"])
    assert denied_reopen.status_code == 403

    admin_reopen = client.post(f"/api/v1/topics/{topic['id']}/reopen", headers=admin_headers)
    assert admin_reopen.status_code == 200
    assert admin_reopen.json()["status"] == "open"

    admin_patch = client.patch(
        f"/api/v1/topics/{topic['id']}",
        headers=admin_headers,
        json={"pinned": True},
    )
    assert admin_patch.status_code == 200
    assert admin_patch.json()["pinned"] is True

    denied_delete = client.delete(f"/api/v1/topics/{topic['id']}", headers=reviewer["headers"])
    assert denied_delete.status_code == 403


def test_topic_list_includes_last_comment_author(client, auth_headers, reviewer, project):
    topic = _create_topic(client, auth_headers, project)
    listing = client.get(f"/api/v1/projects/{project['id']}/topics", headers=auth_headers)
    assert listing.json()[0]["last_comment_author_agent_id"] is None

    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "host 开场"},
    )
    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=reviewer["headers"],
        json={"body": "reviewer 插话"},
    )

    listing = client.get(f"/api/v1/projects/{project['id']}/topics", headers=auth_headers)
    row = next(item for item in listing.json() if item["id"] == topic["id"])
    assert row["last_comment_author_agent_id"] == reviewer["id"]
    assert row["comment_count"] == 2
    assert row["last_comment_id"] is not None
    assert row["last_comment_author_name"] == "reviewer-agent"
    assert row["last_comment_excerpt"] == "reviewer 插话"
    assert row["my_comment_count"] == 1


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


def test_topic_detail_backfills_legacy_null_comment_seq(client, auth_headers, project, db_session):
    topic = _create_topic(client, auth_headers, project)
    first = client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "legacy top-level"},
    )
    assert first.status_code == 201
    second = client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "legacy reply", "parent_id": first.json()["id"]},
    )
    assert second.status_code == 201

    rows = list(
        db_session.scalars(
            select(TopicComment)
            .where(TopicComment.topic_id == uuid.UUID(topic["id"]))
            .order_by(TopicComment.created_at.asc(), TopicComment.id.asc())
        )
    )
    rows[0].comment_seq = None
    rows[1].comment_seq = None

    detail = client.get(f"/api/v1/topics/{topic['id']}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    root = detail.json()["comments"][0]
    seqs = [root["comment_seq"], root["children"][0]["comment_seq"]]
    assert sorted(seqs) == [1, 2]


def test_topic_resolve_records_decision_and_action_items(client, auth_headers, reviewer, project):
    topic = _create_topic(client, auth_headers, project, title="决策话题")
    action_owner_id = reviewer["id"]

    resolved = client.post(
        f"/api/v1/topics/{topic['id']}/resolve",
        headers=auth_headers,
        json={
            "decision": "采用结构化结论层",
            "rationale": "讨论需要沉淀为可执行状态",
            "rejected_options": "继续只依赖评论 thread",
            "open_questions": "后续是否需要版本化结论",
            "action_items": [
                {
                    "title": "补 Web 展示",
                    "description": "在话题页展示当前结论",
                    "owner_agent_id": action_owner_id,
                }
            ],
        },
    )
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["topic_id"] == topic["id"]
    assert body["decision"] == "采用结构化结论层"
    assert body["author_name"] == "test-agent"
    assert len(body["action_items"]) == 1
    assert body["action_items"][0]["owner_agent_id"] == action_owner_id
    assert body["action_items"][0]["owner_name"] == "reviewer-agent"
    assert body["action_items"][0]["status"] == "open"

    detail = client.get(f"/api/v1/topics/{topic['id']}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["decision"]["decision"] == "采用结构化结论层"

    decisions = client.get(f"/api/v1/projects/{project['id']}/decisions", headers=auth_headers)
    assert decisions.status_code == 200
    assert decisions.json()[0]["topic_title"] == "决策话题"

    actions = client.get(
        f"/api/v1/projects/{project['id']}/action-items",
        headers=auth_headers,
        params={"owner_agent_id": action_owner_id, "status": "open"},
    )
    assert actions.status_code == 200
    assert len(actions.json()) == 1
    assert actions.json()[0]["title"] == "补 Web 展示"

    reviewer_todos = client.get("/api/v1/agents/me/todos", headers=reviewer["headers"])
    assert reviewer_todos.status_code == 200
    assert reviewer_todos.json()["action_items"][0]["topic_title"] == "决策话题"


def test_resolve_topic_requires_host_or_admin(client, auth_headers, reviewer, admin_headers, project):
    topic = _create_topic(client, auth_headers, project)

    denied = client.post(
        f"/api/v1/topics/{topic['id']}/resolve",
        headers=reviewer["headers"],
        json={"decision": "非主持尝试写结论"},
    )
    assert denied.status_code == 403

    allowed = client.post(
        f"/api/v1/topics/{topic['id']}/resolve",
        headers=admin_headers,
        json={"no_decision_reason": "管理员记录暂无结论"},
    )
    assert allowed.status_code == 200
    assert allowed.json()["no_decision_reason"] == "管理员记录暂无结论"


def test_topic_resolve_upserts_and_replaces_action_items(client, auth_headers, reviewer, project):
    # A1 resolve 二次约束：旧 resolve 是「delete + reinsert」，等价于删除重建，无 done
    # 写入路径。新语义：
    #   - payload 中带 id 且命中旧项 → 字段更新，status 保留
    #   - payload 中不带旧 id → 旧项若 open 自动 close 成 done（写 audit）
    #   - payload 中无 id 的新项 → 插入，status=open
    topic = _create_topic(client, auth_headers, project)
    first = client.post(
        f"/api/v1/topics/{topic['id']}/resolve",
        headers=auth_headers,
        json={
            "decision": "第一版结论",
            "action_items": [{"title": "旧行动项", "owner_agent_id": reviewer["id"]}],
        },
    )
    assert first.status_code == 200
    decision_id = first.json()["id"]
    old_action_id = first.json()["action_items"][0]["id"]

    # 重跑 resolve，旧 id 不在新 payload → 应自动 done（写 audit），新项以 open 插入
    second = client.post(
        f"/api/v1/topics/{topic['id']}/resolve",
        headers=auth_headers,
        json={
            "decision": "第二版结论",
            "action_items": [{"title": "新行动项", "owner_agent_id": reviewer["id"]}],
        },
    )
    assert second.status_code == 200
    assert second.json()["id"] == decision_id
    assert second.json()["decision"] == "第二版结论"
    items_by_id = {item["id"]: item for item in second.json()["action_items"]}
    assert items_by_id[old_action_id]["status"] == "done"  # 二次约束触发 close
    new_action_ids = [i for i in items_by_id if i != old_action_id]
    assert len(new_action_ids) == 1
    assert items_by_id[new_action_ids[0]]["title"] == "新行动项"
    assert items_by_id[new_action_ids[0]]["status"] == "open"

    # 同一 resolve payload 再跑一次 → 旧项已 done，不再写 audit（幂等）
    audit_logs = client.get(
        "/api/v1/audit",
        headers=auth_headers,
        params={"target_type": "topic_action_item", "target_id": old_action_id},
    )
    completed_events = [
        log for log in audit_logs.json() if log["action"] == "action_item.completed"
    ]
    assert len(completed_events) == 1


def test_topic_resolve_upsert_preserves_status_and_updates_fields(client, auth_headers, admin_headers, reviewer, project):
    """Resolve 时 payload 带 id → upsert：字段更新但 status 不会被 reset。"""
    topic = _create_topic(client, auth_headers, project)
    first = client.post(
        f"/api/v1/topics/{topic['id']}/resolve",
        headers=auth_headers,
        json={
            "decision": "v1",
            "action_items": [
                {"title": "原标题", "owner_agent_id": reviewer["id"], "category": "implementation"}
            ],
        },
    )
    action_id = first.json()["action_items"][0]["id"]

    completed = client.post(
        f"/api/v1/action-items/{action_id}/complete",
        headers=admin_headers,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "done"

    # Re-resolve 带相同 id 但新 title → 字段更新，status 仍是 done
    second = client.post(
        f"/api/v1/topics/{topic['id']}/resolve",
        headers=auth_headers,
        json={
            "decision": "v2",
            "action_items": [
                {
                    "id": action_id,
                    "title": "新标题（手动 done 后不可被 resolve 覆盖）",
                    "owner_agent_id": reviewer["id"],
                    "category": "decision",
                }
            ],
        },
    )
    assert second.status_code == 200
    items = {i["id"]: i for i in second.json()["action_items"]}
    assert items[action_id]["status"] == "done"
    assert items[action_id]["title"] == "新标题（手动 done 后不可被 resolve 覆盖）"
    assert items[action_id]["category"] == "decision"


def test_experiment_linked_to_topic(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project)

    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "正式实验",
            "plan": {"content_md": make_valid_plan(body="plan")},
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
        json={"title": "x", "plan": {"content_md": make_valid_plan(body="p")}, "topic_id": other_topic["id"]},
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
        json={"title": "唯一活跃实验", "plan": {"content_md": make_valid_plan(body="p")}, "topic_id": topic["id"]},
    )
    assert first.status_code == 201

    second = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "重复实验", "plan": {"content_md": make_valid_plan(body="p2")}, "topic_id": topic["id"]},
    )
    assert second.status_code == 409

    client.post(f"/api/v1/experiments/{first.json()['id']}/cancel", headers=auth_headers)
    third = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "取消后可再建", "plan": {"content_md": make_valid_plan(body="p3")}, "topic_id": topic["id"]},
    )
    assert third.status_code == 201


def test_cannot_create_experiment_on_closed_topic(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project)
    closed = client.post(f"/api/v1/topics/{topic['id']}/close", headers=auth_headers)
    assert closed.status_code == 200

    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "关闭后实验", "plan": {"content_md": make_valid_plan(body="p")}, "topic_id": topic["id"]},
    )
    assert resp.status_code == 409
    assert "closed" in resp.json()["detail"].lower()


def test_cannot_close_topic_while_linked_experiment_active(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project)
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "未完成实验", "plan": {"content_md": make_valid_plan(body="p")}, "topic_id": topic["id"]},
    )
    assert exp.status_code == 201

    closed = client.post(f"/api/v1/topics/{topic['id']}/close", headers=auth_headers)

    assert closed.status_code == 409
    assert "linked experiment" in closed.json()["detail"]
    assert "complete or cancel" in closed.json()["detail"]


def test_can_close_topic_after_linked_experiment_cancelled(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project)
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "取消后关话题", "plan": {"content_md": make_valid_plan(body="p")}, "topic_id": topic["id"]},
    )
    assert exp.status_code == 201
    cancelled = client.post(f"/api/v1/experiments/{exp.json()['id']}/cancel", headers=auth_headers)
    assert cancelled.status_code == 200

    closed = client.post(f"/api/v1/topics/{topic['id']}/close", headers=auth_headers)

    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"


def test_can_close_topic_after_linked_experiment_done(client, db_session, auth_headers, project):
    from server.domain.models import Experiment, ExperimentPhase

    topic = _create_topic(client, auth_headers, project)
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "完成后关话题", "plan": {"content_md": make_valid_plan(body="p")}, "topic_id": topic["id"]},
    )
    assert exp.status_code == 201
    row = db_session.get(Experiment, uuid.UUID(exp.json()["id"]))
    row.phase = ExperimentPhase.done
    db_session.commit()

    closed = client.post(f"/api/v1/topics/{topic['id']}/close", headers=auth_headers)

    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"


def test_only_topic_host_can_create_experiment_from_topic(client, auth_headers, reviewer, project):
    topic = _create_topic(client, auth_headers, project)

    denied = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=reviewer["headers"],
        json={"title": "非主持抢开", "plan": {"content_md": make_valid_plan(body="p")}, "topic_id": topic["id"]},
    )
    assert denied.status_code == 403
    assert "host" in denied.json()["detail"].lower()

    allowed = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "主持开实验", "plan": {"content_md": make_valid_plan(body="p")}, "topic_id": topic["id"]},
    )
    assert allowed.status_code == 201
    assert allowed.json()["warnings"] == ["topic_not_ready_for_experiment"]


def test_ready_topic_create_experiment_has_no_not_ready_warning(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project)
    client.post(f"/api/v1/topics/{topic['id']}/advance-round", headers=auth_headers)
    client.post(
        f"/api/v1/topics/{topic['id']}/advance-round",
        headers=auth_headers,
        json={"mark_ready": True},
    )

    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "ready 后开实验", "plan": {"content_md": make_valid_plan(body="p")}, "topic_id": topic["id"]},
    )
    assert resp.status_code == 201
    assert resp.json()["warnings"] == []


def test_admin_can_create_experiment_from_others_topic(client, admin_headers, auth_headers, project):
    topic = _create_topic(client, auth_headers, project, title="他人主持的话题")

    resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=admin_headers,
        json={"title": "管理员代开", "plan": {"content_md": make_valid_plan(body="p")}, "topic_id": topic["id"]},
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


def test_experiment_archive_rejects_active_phase(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project, title="归档后重开实验")
    first = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "第一个", "plan": {"content_md": make_valid_plan(body="p")}, "topic_id": topic["id"]},
    )
    assert first.status_code == 201
    exp_id = first.json()["id"]

    archived = client.patch(
        f"/api/v1/experiments/{exp_id}",
        headers=auth_headers,
        json={"archived": True},
    )
    assert archived.status_code == 409
    assert "complete or cancel" in archived.json()["detail"]

    second = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "第二个", "plan": {"content_md": make_valid_plan(body="p2")}, "topic_id": topic["id"]},
    )
    assert second.status_code == 409, second.text


def test_experiment_archive_allows_new_active_on_topic_after_cancelled(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project, title="取消归档后重开实验")
    first = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "第一个", "plan": {"content_md": make_valid_plan(body="p")}, "topic_id": topic["id"]},
    )
    assert first.status_code == 201
    exp_id = first.json()["id"]
    cancelled = client.post(f"/api/v1/experiments/{exp_id}/cancel", headers=auth_headers)
    assert cancelled.status_code == 200

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
        json={"title": "第二个", "plan": {"content_md": make_valid_plan(body="p2")}, "topic_id": topic["id"]},
    )
    assert second.status_code == 201, second.text


def _participant_comment(client, headers, topic_id: str, body: str = "participant opinion"):
    return client.post(
        f"/api/v1/topics/{topic_id}/comments",
        headers=headers,
        json={"body": body},
    )


def test_host_only_topic_advance(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project)
    resp = client.post(f"/api/v1/topics/{topic['id']}/advance-round", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["discussion_round"] == "round2"


def test_advance_round_ack_dynamic(client, auth_headers, reviewer, project):
    topic = _create_topic(client, auth_headers, project)
    assert _participant_comment(client, reviewer["headers"], topic["id"]).status_code == 201

    pending = client.post(
        f"/api/v1/topics/{topic['id']}/advance-round",
        headers=auth_headers,
        json={"acknowledged_by": []},
    )
    assert pending.status_code == 409
    assert pending.json()["reason"] == "ack_pending"

    ack = client.post(
        f"/api/v1/topics/{topic['id']}/advance-round",
        headers=reviewer["headers"],
        json={"ack": "accept"},
    )
    assert ack.status_code == 200

    advanced = client.post(
        f"/api/v1/topics/{topic['id']}/advance-round",
        headers=auth_headers,
        json={"acknowledged_by": [reviewer["id"]]},
    )
    assert advanced.status_code == 200
    assert advanced.json()["discussion_round"] == "round2"


def test_round_summary_comment_sets_pending_ack_and_todos(client, auth_headers, reviewer, project):
    topic = _create_topic(client, auth_headers, project)
    assert _participant_comment(client, reviewer["headers"], topic["id"]).status_code == 201

    summary = client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "## Round 1 Summary\n\n### 已共识\n- 混合方案\n"},
    )
    assert summary.status_code == 201, summary.text

    detail = client.get(f"/api/v1/topics/{topic['id']}", headers=auth_headers)
    assert detail.json()["advance_round_pending_since"] is not None

    reviewer_todos = client.get("/api/v1/agents/me/todos", headers=reviewer["headers"]).json()
    assert len(reviewer_todos["pending_round_acks"]) == 1
    assert reviewer_todos["pending_round_acks"][0]["topic_id"] == topic["id"]
    assert reviewer_todos["pending_round_acks"][0]["summary_comment_id"] == summary.json()["id"]

    client.post(
        f"/api/v1/topics/{topic['id']}/advance-round",
        headers=reviewer["headers"],
        json={"ack": "accept"},
    )
    reviewer_todos_after = client.get("/api/v1/agents/me/todos", headers=reviewer["headers"]).json()
    assert reviewer_todos_after["pending_round_acks"] == []


def test_ack_rejected_409(client, auth_headers, reviewer, project):
    topic = _create_topic(client, auth_headers, project)
    _participant_comment(client, reviewer["headers"], topic["id"])
    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "## Round 1 Summary\n\n### 已共识\n- x\n"},
    )
    client.post(
        f"/api/v1/topics/{topic['id']}/advance-round",
        headers=reviewer["headers"],
        json={"ack": "reject"},
    )

    resp = client.post(
        f"/api/v1/topics/{topic['id']}/advance-round",
        headers=auth_headers,
        json={"acknowledged_by": [reviewer["id"]]},
    )
    assert resp.status_code == 409
    assert resp.json()["reason"] == "ack_rejected"


def test_ack_reject_superseded_by_accept_on_revised_summary(client, auth_headers, reviewer, project):
    topic = _create_topic(client, auth_headers, project)
    _participant_comment(client, reviewer["headers"], topic["id"])

    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "## Round 1 Summary\n\n### 已共识\n- v1\n"},
    )
    client.post(
        f"/api/v1/topics/{topic['id']}/advance-round",
        headers=reviewer["headers"],
        json={"ack": "reject"},
    )
    client.post(
        f"/api/v1/topics/{topic['id']}/comments",
        headers=auth_headers,
        json={"body": "## Round 1 Summary v2\n\n### 已共识\n- v2\n"},
    )
    client.post(
        f"/api/v1/topics/{topic['id']}/advance-round",
        headers=reviewer["headers"],
        json={"ack": "accept"},
    )

    advanced = client.post(
        f"/api/v1/topics/{topic['id']}/advance-round",
        headers=auth_headers,
        json={"acknowledged_by": [reviewer["id"]]},
    )
    assert advanced.status_code == 200
    assert advanced.json()["discussion_round"] == "round2"


def test_ack_timeout_silence_consent(client, db_session, auth_headers, reviewer, project):
    import uuid
    from datetime import UTC, datetime, timedelta

    from server.domain.models import Topic

    topic = _create_topic(client, auth_headers, project)
    _participant_comment(client, reviewer["headers"], topic["id"])

    pending = client.post(
        f"/api/v1/topics/{topic['id']}/advance-round",
        headers=auth_headers,
        json={"acknowledged_by": []},
    )
    assert pending.status_code == 409

    row = db_session.get(Topic, uuid.UUID(topic["id"]))
    row.advance_round_pending_since = datetime.now(UTC) - timedelta(hours=25)
    db_session.commit()

    advanced = client.post(
        f"/api/v1/topics/{topic['id']}/advance-round",
        headers=auth_headers,
        json={"acknowledged_by": []},
    )
    assert advanced.status_code == 200


def test_ack_set_excludes_dismissed(client, auth_headers, reviewer, project):
    topic = _create_topic(client, auth_headers, project)
    _participant_comment(client, reviewer["headers"], topic["id"])
    client.post(
        f"/api/v1/topics/{topic['id']}/advance-round",
        headers=reviewer["headers"],
        json={"ack": "dismiss"},
    )

    resp = client.post(f"/api/v1/topics/{topic['id']}/advance-round", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["discussion_round"] == "round2"


def test_ack_set_excludes_archived_topic(client, auth_headers, reviewer, project):
    topic = _create_topic(client, auth_headers, project)
    _participant_comment(client, reviewer["headers"], topic["id"])
    archived = client.patch(
        f"/api/v1/topics/{topic['id']}",
        headers=auth_headers,
        json={"archived": True},
    )
    assert archived.status_code == 200

    resp = client.post(f"/api/v1/topics/{topic['id']}/advance-round", headers=auth_headers)
    assert resp.status_code == 409
    assert resp.json()["reason"] == "archived_topic"


def test_dismiss_after_advance_init(client, auth_headers, reviewer, project):
    topic = _create_topic(client, auth_headers, project)
    _participant_comment(client, reviewer["headers"], topic["id"])

    pending = client.post(
        f"/api/v1/topics/{topic['id']}/advance-round",
        headers=auth_headers,
        json={"acknowledged_by": []},
    )
    assert pending.status_code == 409

    client.post(
        f"/api/v1/topics/{topic['id']}/advance-round",
        headers=reviewer["headers"],
        json={"ack": "dismiss"},
    )

    advanced = client.post(f"/api/v1/topics/{topic['id']}/advance-round", headers=auth_headers)
    assert advanced.status_code == 200


def test_archived_topic_advance_rejected(client, auth_headers, project):
    topic = _create_topic(client, auth_headers, project)
    client.patch(
        f"/api/v1/topics/{topic['id']}",
        headers=auth_headers,
        json={"archived": True},
    )
    resp = client.post(f"/api/v1/topics/{topic['id']}/advance-round", headers=auth_headers)
    assert resp.status_code == 409
    assert resp.json()["reason"] == "archived_topic"
