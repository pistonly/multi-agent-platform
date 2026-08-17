"""A1 acceptance tests for the action-item lifecycle endpoints.

Covers CLI/API surface for ``complete`` and ``cancel``, the audit payload
contract (``cancel_reason`` / ``triggered_by`` / ``category``), and the
permissions + idempotency rules.

v0.13 M58: topics and decisions are inserted via ORM (topic write endpoints
retired); resolve-upsert semantics tests were removed with the endpoint.
"""

import uuid

from map_types.enums import TopicStatus
from sqlalchemy import select

from server.domain.models import Agent
from tests._db_topic_factory import db_create_topic, db_resolve_with_action_items
from tests._frontmatter import make_valid_plan


def _host(db):
    return db.scalar(select(Agent).where(Agent.name == "test-agent"))


def _create_topic(db, project, **overrides):
    overrides.setdefault("title", "action-item test")
    overrides.setdefault("description", "tests for complete/cancel")
    return db_create_topic(
        db,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=_host(db).id,
        **overrides,
    )


def _resolve_with_action_items(db, topic, action_items, decision="d"):
    return db_resolve_with_action_items(
        db, topic, author=_host(db), action_items=action_items, decision=decision
    )


def _close_topic(db, topic):
    topic.status = TopicStatus.closed
    db.commit()


def test_complete_action_item_happy_path(client, db_session, auth_headers, admin_headers, reviewer, project):
    """A1-1 + A1-7: complete succeeds; audit event records the transition."""
    topic = _create_topic(db_session, project)
    items = _resolve_with_action_items(
        db_session,
        topic,
        [{"title": "做 A1 验收", "owner_agent_id": reviewer["id"]}],
    )
    item_id = items[0].id

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
    assert any(item["id"] == str(item_id) for item in listing.json())


def test_complete_action_item_404_when_missing(client, admin_headers):
    import uuid

    resp = client.post(
        f"/api/v1/action-items/{uuid.uuid4()}/complete",
        headers=admin_headers,
    )
    assert resp.status_code == 404


def test_complete_action_item_409_when_already_done(client, db_session, auth_headers, admin_headers, reviewer, project):
    """A1-6: re-complete returns 409."""
    topic = _create_topic(db_session, project)
    items = _resolve_with_action_items(
        db_session,
        topic,
        [{"title": "重复完成测试", "owner_agent_id": reviewer["id"]}],
    )
    item_id = items[0].id

    first = client.post(f"/api/v1/action-items/{item_id}/complete", headers=admin_headers)
    assert first.status_code == 200

    second = client.post(f"/api/v1/action-items/{item_id}/complete", headers=admin_headers)
    assert second.status_code == 409


def test_complete_action_item_403_when_not_owner_or_admin(client, db_session, auth_headers, admin_headers, reviewer, project):
    """A1-5: caller must be owner or admin."""
    outsider_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={"name": "outsider", "role": "agent", "project_key": project["project_key"]},
    )
    outsider_h = {"Authorization": f"Bearer {outsider_resp.json()['api_token']}"}

    topic = _create_topic(db_session, project)
    items = _resolve_with_action_items(
        db_session,
        topic,
        [{"title": "权限测试", "owner_agent_id": reviewer["id"]}],
    )
    item_id = items[0].id

    resp = client.post(f"/api/v1/action-items/{item_id}/complete", headers=outsider_h)
    assert resp.status_code == 403


def test_cancel_reason_min_length_enforced_at_api_layer(client, db_session, auth_headers, admin_headers, reviewer, project):
    """A1-4: API rejects short reasons even if CLI is bypassed."""
    topic = _create_topic(db_session, project)
    items = _resolve_with_action_items(
        db_session,
        topic,
        [{"title": "API 长度校验", "owner_agent_id": reviewer["id"]}],
    )
    item_id = items[0].id

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


def test_cancel_reason_min_length_decision_category(client, db_session, auth_headers, admin_headers, reviewer, project):
    """A1-4 决策类 ≥16 字符校验。"""
    topic = _create_topic(db_session, project)
    items = _resolve_with_action_items(
        db_session,
        topic,
        [{"title": "决策类长度", "owner_agent_id": reviewer["id"]}],
    )
    item_id = items[0].id

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


def test_cancel_emits_audit_event_with_payload_fields(client, db_session, auth_headers, admin_headers, reviewer, project):
    """A1-7: cancel_reason lands in audit_logs.payload_json."""
    topic = _create_topic(db_session, project)
    items = _resolve_with_action_items(
        db_session,
        topic,
        [{"title": "audit 测试", "owner_agent_id": reviewer["id"]}],
    )
    item_id = items[0].id
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


def test_complete_emits_audit_event_with_prev_new_status(client, db_session, auth_headers, admin_headers, reviewer, project):
    topic = _create_topic(db_session, project)
    items = _resolve_with_action_items(
        db_session,
        topic,
        [{"title": "complete audit", "owner_agent_id": reviewer["id"]}],
    )
    item_id = items[0].id

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


def test_deliver_action_item_on_closed_topic(client, db_session, auth_headers, admin_headers, reviewer, project):
    topic = _create_topic(db_session, project)
    items = _resolve_with_action_items(
        db_session,
        topic,
        [{"title": "closed topic deliver", "owner_agent_id": reviewer["id"]}],
    )
    item_id = items[0].id
    _close_topic(db_session, topic)

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


def test_deliver_action_item_on_archived_topic(client, db_session, auth_headers, admin_headers, reviewer, project):
    topic = _create_topic(db_session, project)
    items = _resolve_with_action_items(
        db_session,
        topic,
        [{"title": "archived topic deliver", "owner_agent_id": reviewer["id"]}],
    )
    item_id = items[0].id
    client.patch(
        f"/api/v1/topics/{topic.id}",
        headers=auth_headers,
        json={"archived": True},
    )

    delivered = client.post(
        f"/api/v1/action-items/{item_id}/deliver",
        headers=admin_headers,
    )
    assert delivered.status_code == 200


def test_deliver_action_item_404_when_topic_deleted(
    client, db_session, auth_headers, admin_headers, reviewer, project
):
    topic = _create_topic(db_session, project)
    items = _resolve_with_action_items(
        db_session,
        topic,
        [{"title": "deleted topic deliver", "owner_agent_id": reviewer["id"]}],
    )
    item_id = items[0].id
    # DELETE 端点已退役（M58 410）；软删语义保留，直插 deleted_at
    from datetime import datetime, timezone

    topic.deleted_at = datetime.now(timezone.utc)
    db_session.commit()

    resp = client.post(
        f"/api/v1/action-items/{item_id}/deliver",
        headers=admin_headers,
    )
    assert resp.status_code == 404


def test_migrate_closed_topic_action_item_cascade_backlog(
    client, db_session, auth_headers, admin_headers, reviewer, project, agent_token
):
    from server.domain.models import TopicActionItem
    from server.services import action_item_migration_service as mig

    owner_id = agent_token[0]
    topic = _create_topic(db_session, project, title="Migrate Closed Topic")
    items = _resolve_with_action_items(
        db_session,
        topic,
        [{"title": "Migrate Closed Topic", "owner_agent_id": owner_id}],
    )
    item_id = items[0].id
    _close_topic(db_session, topic)

    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "Migrate Closed Topic",
            "plan": {"content_md": make_valid_plan(body="p")},
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

    row = db_session.get(TopicActionItem, item_id)
    assert row.status.value == "open"

    item = row
    first = mig.migrate_action_item(db_session, item)
    assert first.strategy == "cascade_backlog"
    db_session.refresh(item)
    assert item.status.value == "done"

    second = mig.migrate_action_item(db_session, item)
    assert second.skipped is True


def test_migrate_action_item_to_topic_opt_in(client, db_session, auth_headers, reviewer, project):
    from server.domain.models import TopicActionItem
    from server.services import action_item_migration_service as mig

    closed_topic = _create_topic(db_session, project, title="closed-src")
    items = _resolve_with_action_items(
        db_session,
        closed_topic,
        [{"title": "move me", "owner_agent_id": reviewer["id"]}],
    )
    item_id = items[0].id
    _close_topic(db_session, closed_topic)

    target = _create_topic(db_session, project, title="open-target")
    item = db_session.get(TopicActionItem, item_id)
    result = mig.migrate_action_item(
        db_session,
        item,
        migrate_to_topic_id=target.id,
    )
    assert result.strategy == "migrate_to"
    db_session.refresh(item)
    assert item.topic_id == target.id
