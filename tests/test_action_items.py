"""A1 acceptance tests for the action-item lifecycle endpoints.

Covers CLI/API surface for ``complete`` and ``cancel``, the audit payload
contract (``cancel_reason`` / ``triggered_by`` / ``category``), and the
permissions + idempotency rules.
"""

import uuid


def _create_topic(client, headers, project, **overrides):
    payload = {"title": "action-item test", "description": "tests for complete/cancel"}
    payload.update(overrides)
    resp = client.post(f"/api/v1/projects/{project['id']}/topics", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _resolve_with_action_items(client, headers, topic_id, action_items, decision="d"):
    resp = client.post(
        f"/api/v1/topics/{topic_id}/resolve",
        headers=headers,
        json={"decision": decision, "action_items": action_items},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_complete_action_item_happy_path(client, auth_headers, admin_headers, reviewer, project):
    """A1-1 + A1-7: complete succeeds; audit event records the transition."""
    topic = _create_topic(client, auth_headers, project)
    resolved = _resolve_with_action_items(
        client,
        auth_headers,
        topic["id"],
        [{"title": "做 A1 验收", "owner_agent_id": reviewer["id"]}],
    )
    item_id = resolved["action_items"][0]["id"]

    completed = client.post(
        f"/api/v1/action-items/{item_id}/complete",
        headers=admin_headers,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "done"

    listing = client.get(
        f"/api/v1/projects/{project['id']}/action-items",
        headers=auth_headers,
        params={"status": "done"},
    )
    assert any(item["id"] == item_id for item in listing.json())


def test_complete_action_item_404_when_missing(client, admin_headers):
    import uuid

    resp = client.post(
        f"/api/v1/action-items/{uuid.uuid4()}/complete",
        headers=admin_headers,
    )
    assert resp.status_code == 404


def test_complete_action_item_409_when_already_done(client, auth_headers, admin_headers, reviewer, project):
    """A1-6: re-complete returns 409."""
    topic = _create_topic(client, auth_headers, project)
    resolved = _resolve_with_action_items(
        client,
        auth_headers,
        topic["id"],
        [{"title": "重复完成测试", "owner_agent_id": reviewer["id"]}],
    )
    item_id = resolved["action_items"][0]["id"]

    first = client.post(f"/api/v1/action-items/{item_id}/complete", headers=admin_headers)
    assert first.status_code == 200

    second = client.post(f"/api/v1/action-items/{item_id}/complete", headers=admin_headers)
    assert second.status_code == 409


def test_complete_action_item_403_when_not_owner_or_admin(client, auth_headers, admin_headers, reviewer, project):
    """A1-5: caller must be owner or admin."""
    outsider_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        params={"name": "outsider", "role": "agent", "project_key": project["project_key"]},
    )
    outsider_h = {"Authorization": f"Bearer {outsider_resp.json()['api_token']}"}

    topic = _create_topic(client, auth_headers, project)
    resolved = _resolve_with_action_items(
        client,
        auth_headers,
        topic["id"],
        [{"title": "权限测试", "owner_agent_id": reviewer["id"]}],
    )
    item_id = resolved["action_items"][0]["id"]

    resp = client.post(f"/api/v1/action-items/{item_id}/complete", headers=outsider_h)
    assert resp.status_code == 403


def test_cancel_reason_min_length_enforced_at_api_layer(client, auth_headers, admin_headers, reviewer, project):
    """A1-4: API rejects short reasons even if CLI is bypassed."""
    topic = _create_topic(client, auth_headers, project)
    resolved = _resolve_with_action_items(
        client,
        auth_headers,
        topic["id"],
        [{"title": "API 长度校验", "owner_agent_id": reviewer["id"]}],
    )
    item_id = resolved["action_items"][0]["id"]

    short = client.post(
        f"/api/v1/action-items/{item_id}/cancel",
        headers=admin_headers,
        json={"reason": "x"},
    )
    assert short.status_code == 422, short.text

    decision_short = client.post(
        f"/api/v1/action-items/{item_id}/cancel",
        headers=admin_headers,
        json={"reason": "12字符1234", "category": "decision"},
    )
    assert decision_short.status_code == 422

    ok = client.post(
        f"/api/v1/action-items/{item_id}/cancel",
        headers=admin_headers,
        json={"reason": "方向调整，不再需要"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "cancelled"
    assert ok.json()["cancel_reason"] == "方向调整，不再需要"


def test_cancel_reason_min_length_decision_category(client, auth_headers, admin_headers, reviewer, project):
    """A1-4 决策类 ≥16 字符校验。"""
    topic = _create_topic(client, auth_headers, project)
    resolved = _resolve_with_action_items(
        client,
        auth_headers,
        topic["id"],
        [{"title": "决策类长度", "owner_agent_id": reviewer["id"]}],
    )
    item_id = resolved["action_items"][0]["id"]

    ok = client.post(
        f"/api/v1/action-items/{item_id}/cancel",
        headers=admin_headers,
        json={
            "reason": "经评审决定改用 webhook 替代轮询，降低运维负担",
            "category": "decision",
        },
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["category"] == "decision"


def test_cancel_emits_audit_event_with_payload_fields(client, auth_headers, admin_headers, reviewer, project):
    """A1-7: cancel_reason lands in audit_logs.payload_json."""
    topic = _create_topic(client, auth_headers, project)
    resolved = _resolve_with_action_items(
        client,
        auth_headers,
        topic["id"],
        [{"title": "audit 测试", "owner_agent_id": reviewer["id"]}],
    )
    item_id = resolved["action_items"][0]["id"]
    reason = "本次议题合并到上游重做"

    cancelled = client.post(
        f"/api/v1/action-items/{item_id}/cancel",
        headers=admin_headers,
        json={"reason": reason, "category": "implementation"},
    )
    assert cancelled.status_code == 200

    audit = client.get(
        "/api/v1/audit",
        headers=auth_headers,
        params={"target_type": "topic_action_item", "target_id": item_id},
    )
    payload_matches = [
        log
        for log in audit.json()
        if log["action"] == "action_item.cancelled"
        and log.get("payload_json", {}).get("cancel_reason") == reason
    ]
    assert len(payload_matches) == 1
    assert payload_matches[0]["payload_json"]["category"] == "implementation"
    assert payload_matches[0]["payload_json"]["triggered_by"] == "manual"
    assert payload_matches[0]["payload_json"]["new_status"] == "cancelled"


def test_complete_emits_audit_event_with_prev_new_status(client, auth_headers, admin_headers, reviewer, project):
    topic = _create_topic(client, auth_headers, project)
    resolved = _resolve_with_action_items(
        client,
        auth_headers,
        topic["id"],
        [{"title": "complete audit", "owner_agent_id": reviewer["id"]}],
    )
    item_id = resolved["action_items"][0]["id"]

    client.post(f"/api/v1/action-items/{item_id}/complete", headers=admin_headers)

    audit = client.get(
        "/api/v1/audit",
        headers=auth_headers,
        params={"target_type": "topic_action_item", "target_id": item_id},
    )
    completed = [log for log in audit.json() if log["action"] == "action_item.completed"]
    assert len(completed) == 1
    payload = completed[0]["payload_json"]
    assert payload["prev_status"] == "open"
    assert payload["new_status"] == "done"
    assert payload["triggered_by"] == "manual"


def test_resolve_secondary_constraint_closes_old_action_item(client, auth_headers, reviewer, project):
    """A1-8: 重跑 resolve，旧 open 项不在新 payload 中 → 自动 done，audit 一次。"""
    topic = _create_topic(client, auth_headers, project)
    first = _resolve_with_action_items(
        client,
        auth_headers,
        topic["id"],
        [{"title": "二次约束测试", "owner_agent_id": reviewer["id"]}],
    )
    old_id = first["action_items"][0]["id"]

    second = _resolve_with_action_items(
        client,
        auth_headers,
        topic["id"],
        [{"title": "新项", "owner_agent_id": reviewer["id"]}],
    )
    items_by_id = {i["id"]: i for i in second["action_items"]}
    assert items_by_id[old_id]["status"] == "done"

    audit = client.get(
        "/api/v1/audit",
        headers=auth_headers,
        params={"target_type": "topic_action_item", "target_id": old_id},
    )
    completed = [
        log
        for log in audit.json()
        if log["action"] == "action_item.completed"
        and log.get("payload_json", {}).get("triggered_by") == "topic_resolve"
    ]
    assert len(completed) == 1

    _resolve_with_action_items(
        client,
        auth_headers,
        topic["id"],
        [{"title": "新项", "owner_agent_id": reviewer["id"]}],
    )
    audit_after = client.get(
        "/api/v1/audit",
        headers=auth_headers,
        params={"target_type": "topic_action_item", "target_id": old_id},
    )
    completed_after = [
        log
        for log in audit_after.json()
        if log["action"] == "action_item.completed"
        and log.get("payload_json", {}).get("triggered_by") == "topic_resolve"
    ]
    assert len(completed_after) == 1  # 没新增


def test_resolve_preserves_existing_done_status_across_runs(client, auth_headers, admin_headers, reviewer, project):
    """A1-9 / 幂等：旧 done 项跨 resolve 仍保持 done。"""
    topic = _create_topic(client, auth_headers, project)
    first = _resolve_with_action_items(
        client,
        auth_headers,
        topic["id"],
        [{"title": "持久完成", "owner_agent_id": reviewer["id"]}],
    )
    item_id = first["action_items"][0]["id"]

    client.post(f"/api/v1/action-items/{item_id}/complete", headers=admin_headers)

    second = _resolve_with_action_items(
        client,
        auth_headers,
        topic["id"],
        [{"title": "another", "owner_agent_id": reviewer["id"]}],
    )
    items = {i["id"]: i for i in second["action_items"]}
    assert items[item_id]["status"] == "done"


def test_deliver_action_item_on_closed_topic(client, auth_headers, admin_headers, reviewer, project):
    topic = _create_topic(client, auth_headers, project)
    resolved = _resolve_with_action_items(
        client,
        auth_headers,
        topic["id"],
        [{"title": "closed topic deliver", "owner_agent_id": reviewer["id"]}],
    )
    item_id = resolved["action_items"][0]["id"]
    client.post(f"/api/v1/topics/{topic['id']}/close", headers=auth_headers)

    delivered = client.post(
        f"/api/v1/action-items/{item_id}/deliver",
        headers=admin_headers,
    )
    assert delivered.status_code == 200, delivered.text
    assert delivered.json()["status"] == "done"

    audit = client.get(
        "/api/v1/audit",
        headers=auth_headers,
        params={"target_type": "topic_action_item", "target_id": item_id},
    )
    delivered_logs = [log for log in audit.json() if log["action"] == "action_item.delivered"]
    assert len(delivered_logs) == 1
    assert delivered_logs[0]["payload_json"]["topic_status"] == "closed"


def test_deliver_action_item_on_archived_topic(client, auth_headers, admin_headers, reviewer, project):
    topic = _create_topic(client, auth_headers, project)
    resolved = _resolve_with_action_items(
        client,
        auth_headers,
        topic["id"],
        [{"title": "archived topic deliver", "owner_agent_id": reviewer["id"]}],
    )
    item_id = resolved["action_items"][0]["id"]
    client.patch(
        f"/api/v1/topics/{topic['id']}",
        headers=auth_headers,
        json={"archived": True},
    )

    delivered = client.post(
        f"/api/v1/action-items/{item_id}/deliver",
        headers=admin_headers,
    )
    assert delivered.status_code == 200


def test_deliver_action_item_404_when_topic_deleted(
    client, auth_headers, admin_headers, reviewer, project
):
    topic = _create_topic(client, auth_headers, project)
    resolved = _resolve_with_action_items(
        client,
        auth_headers,
        topic["id"],
        [{"title": "deleted topic deliver", "owner_agent_id": reviewer["id"]}],
    )
    item_id = resolved["action_items"][0]["id"]
    client.delete(f"/api/v1/topics/{topic['id']}", headers=auth_headers)

    resp = client.post(
        f"/api/v1/action-items/{item_id}/deliver",
        headers=admin_headers,
    )
    assert resp.status_code == 404


def test_resolve_rebinds_topic_id_on_existing_item(
    client, auth_headers, reviewer, project, db_session
):
    from server.domain.models import TopicActionItem

    topic = _create_topic(client, auth_headers, project)
    first = _resolve_with_action_items(
        client,
        auth_headers,
        topic["id"],
        [{"title": "rebind test", "owner_agent_id": reviewer["id"]}],
    )
    item_id = first["action_items"][0]["id"]
    other = _create_topic(client, auth_headers, project, title="other-topic")
    row = db_session.get(TopicActionItem, uuid.UUID(item_id))
    row.topic_id = uuid.UUID(other["id"])
    db_session.commit()

    second = _resolve_with_action_items(
        client,
        auth_headers,
        topic["id"],
        [{"id": item_id, "title": "rebind test", "owner_agent_id": reviewer["id"]}],
    )
    rebound = next(i for i in second["action_items"] if i["id"] == item_id)
    assert rebound["topic_id"] == topic["id"]


def test_migrate_closed_topic_action_item_cascade_backlog(
    client, auth_headers, admin_headers, reviewer, project, db_session, agent_token
):
    from server.domain.models import TopicActionItem
    from server.services import action_item_migration_service as mig

    owner_id = agent_token[0]
    topic = _create_topic(client, auth_headers, project, title="Migrate Closed Topic")
    resolved = _resolve_with_action_items(
        client,
        auth_headers,
        topic["id"],
        [{"title": "Migrate Closed Topic", "owner_agent_id": owner_id}],
    )
    item_id = resolved["action_items"][0]["id"]
    client.post(f"/api/v1/topics/{topic['id']}/close", headers=auth_headers)

    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "Migrate Closed Topic",
            "plan": {"content_md": "p"},
            "submit_for_review": True,
        },
    ).json()
    client.post(
        f"/api/v1/experiments/{exp['id']}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["ok"]},
    )
    client.post(f"/api/v1/experiments/{exp['id']}/approve", headers=auth_headers)
    client.post(f"/api/v1/experiments/{exp['id']}/start", headers=auth_headers)
    client.post(
        f"/api/v1/experiments/{exp['id']}/complete",
        headers=auth_headers,
        json={
            "summary": "done",
            "content_md": "ok",
            "metadata": {"pytest_summary": "unit passed"},
        },
    )
    client.post(
        f"/api/v1/experiments/{exp['id']}/accept-result",
        headers=reviewer["headers"],
        json={"summary": "ok", "content_md": "ok"},
    )

    row = db_session.get(TopicActionItem, uuid.UUID(item_id))
    assert row.status.value == "open"

    item = row
    first = mig.migrate_action_item(db_session, item)
    assert first.strategy == "cascade_backlog"
    db_session.refresh(item)
    assert item.status.value == "done"

    second = mig.migrate_action_item(db_session, item)
    assert second.skipped is True


def test_migrate_action_item_to_topic_opt_in(client, auth_headers, reviewer, project, db_session):
    from server.domain.models import TopicActionItem
    from server.services import action_item_migration_service as mig

    closed_topic = _create_topic(client, auth_headers, project, title="closed-src")
    resolved = _resolve_with_action_items(
        client,
        auth_headers,
        closed_topic["id"],
        [{"title": "move me", "owner_agent_id": reviewer["id"]}],
    )
    item_id = resolved["action_items"][0]["id"]
    client.post(f"/api/v1/topics/{closed_topic['id']}/close", headers=auth_headers)

    target = _create_topic(client, auth_headers, project, title="open-target")
    item = db_session.get(TopicActionItem, uuid.UUID(item_id))
    result = mig.migrate_action_item(
        db_session,
        item,
        migrate_to_topic_id=uuid.UUID(target["id"]),
    )
    assert result.strategy == "migrate_to"
    db_session.refresh(item)
    assert str(item.topic_id) == target["id"]
