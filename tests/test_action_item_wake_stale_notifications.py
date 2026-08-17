"""Experiment B / I5: wake + stale notification delivery.

Plan §3 #2 (admin 优先) + #3 (creator audit-only) + §8 B-2 / B-5 / B-8 / B-12.

The ``mark_wake_sent`` / ``mark-stale`` endpoints mutate the action_item
and write the audit row inside ``action_item_service``. I5 layers in-app
notification delivery on top of those calls — the wakeable category surfaces
the bump in the assignee's todos (and the SSE stream), and the stale
notification fans out to every admin agent.

The recipient policy (plan §3):
- ``action_item.wake_sent``  →  owner_agent_id only
- ``action_item.stale``      →  every Agent with role=admin, excluding the
                                owner (avoid double-notification when owner
                                is also admin). The topic creator is
                                intentionally NOT a recipient — I6 audit-only.

The tests below verify each policy by reading the ``notifications`` table
directly via the DB session fixture; the HTTP-level success path is covered
by ``test_action_item_wake_endpoints.py``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow

import uuid

from map_types.enums import NotificationCategory
from sqlalchemy import select

from server.domain.models import Agent, Notification
from tests._db_topic_factory import db_create_topic, db_resolve_with_action_items


def _create_topic(db, project, creator_agent_id):
    """DB-direct topic insert (v0.13 M58: POST /topics retired)."""
    return db_create_topic(
        db,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=uuid.UUID(str(creator_agent_id)),
        title="wake notification test",
        description=None,
    )


def _resolve_with_action_item(db, topic, owner_agent_id):
    author = db.scalar(select(Agent).where(Agent.name == "admin-agent"))
    rows = db_resolve_with_action_items(
        db,
        topic,
        author=author,
        action_items=[{"title": "notify me", "owner_agent_id": owner_agent_id}],
    )
    return {"id": str(rows[0].id)}


def _owner_id_from_auth(client, auth_headers):
    resp = client.get("/api/v1/agents/me", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _admin_id_from_headers(client, admin_headers):
    resp = client.get("/api/v1/agents/me", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _to_uuid(value):
    """Coerce JSON-shaped id strings into ``uuid.UUID`` for SQLAlchemy queries."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _notifications_for(db_session, agent_id):
    rows = db_session.scalars(
        select(Notification).where(Notification.recipient_agent_id == _to_uuid(agent_id))
    ).all()
    return list(rows)


def _events_for(db_session, agent_id):
    return [row.event for row in _notifications_for(db_session, agent_id)]


# ---------------------------------------------------------------------------
# B-2: mark-wake-sent posts a wakeable notification to the owner
# ---------------------------------------------------------------------------


def test_mark_wake_sent_notifies_owner(
    client, db_session, auth_headers, admin_headers, project
):
    owner_id = _owner_id_from_auth(client, auth_headers)
    admin_id = _admin_id_from_headers(client, admin_headers)
    topic = _create_topic(db_session, project, admin_id)
    item = _resolve_with_action_item(db_session, topic, owner_id)

    resp = client.post(
        f"/api/v1/action-items/{item['id']}/mark-wake-sent",
        headers=admin_headers,  # admin is calling so we can attribute the actor
    )
    assert resp.status_code == 200, resp.text

    owner_events = _events_for(db_session, owner_id)
    assert "action_item.wake_sent" in owner_events, (
        f"owner should receive wake notification, got {owner_events}"
    )

    # Admin should NOT receive the wake notification (per plan §3 #3, wakeable
    # is only for the assignee; admin only sees stale transitions).
    admin_events = _events_for(db_session, admin_id)
    assert "action_item.wake_sent" not in admin_events


def test_mark_wake_sent_notification_is_wakeable(
    client, db_session, auth_headers, admin_headers, project
):
    owner_id = _owner_id_from_auth(client, auth_headers)
    admin_id = _admin_id_from_headers(client, admin_headers)
    topic = _create_topic(db_session, project, admin_id)
    item = _resolve_with_action_item(db_session, topic, owner_id)

    client.post(
        f"/api/v1/action-items/{item['id']}/mark-wake-sent",
        headers=admin_headers,
    )

    rows = _notifications_for(db_session, owner_id)
    wake_rows = [r for r in rows if r.event == "action_item.wake_sent"]
    assert len(wake_rows) == 1
    assert wake_rows[0].category == NotificationCategory.wakeable
    assert wake_rows[0].target_type == "topic_action_item"
    assert str(wake_rows[0].target_id) == item["id"]
    # Payload echoes wake_count + topic_id (see helper for the full schema).
    payload = wake_rows[0].payload_json or {}
    assert payload.get("wake_count") == 1
    assert payload.get("action_item_id") == item["id"]
    assert payload.get("topic_id") == str(topic.id)


def test_repeated_wakes_increment_wake_count_in_payload(
    client, db_session, auth_headers, admin_headers, project
):
    owner_id = _owner_id_from_auth(client, auth_headers)
    admin_id = _admin_id_from_headers(client, admin_headers)
    topic = _create_topic(db_session, project, admin_id)
    item = _resolve_with_action_item(db_session, topic, owner_id)

    for _ in range(3):
        resp = client.post(
            f"/api/v1/action-items/{item['id']}/mark-wake-sent",
            headers=admin_headers,
        )
        assert resp.status_code == 200

    # ``notification_service`` dedups by ``group_key`` (recipient × project ×
    # target × event), so repeated wakes of the same action_item collapse to
    # one row whose ``payload_json.wake_count`` and ``event_count`` advance
    # with each bump. The wake_version counter bumps on every wakeable hit,
    # so the runtime-waker reconsiders the notification event on the next
    # cycle until the assignee reads it.
    rows = [r for r in _notifications_for(db_session, owner_id) if r.event == "action_item.wake_sent"]
    assert len(rows) == 1, f"expected one deduped wake notification, got {len(rows)}"
    payload = rows[0].payload_json or {}
    assert payload.get("wake_count") == 3
    assert rows[0].event_count == 3
    assert (rows[0].wake_version or 1) >= 3


# ---------------------------------------------------------------------------
# B-5 / B-12: mark-stale posts a wakeable notification to every admin
# ---------------------------------------------------------------------------


def test_mark_stale_notifies_admin(
    client, db_session, auth_headers, admin_headers, project
):
    owner_id = _owner_id_from_auth(client, auth_headers)
    admin_id = _admin_id_from_headers(client, admin_headers)
    topic = _create_topic(db_session, project, admin_id)
    item = _resolve_with_action_item(db_session, topic, owner_id)

    # Wake first so the stale invariant ("last_woken_at required") passes.
    client.post(
        f"/api/v1/action-items/{item['id']}/mark-wake-sent",
        headers=admin_headers,
    )

    resp = client.post(
        f"/api/v1/action-items/{item['id']}/mark-stale",
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text

    admin_events = _events_for(db_session, admin_id)
    assert "action_item.stale" in admin_events, (
        f"admin should receive stale notification, got {admin_events}"
    )


def test_mark_stale_notification_is_wakeable(
    client, db_session, auth_headers, admin_headers, project
):
    owner_id = _owner_id_from_auth(client, auth_headers)
    admin_id = _admin_id_from_headers(client, admin_headers)
    topic = _create_topic(db_session, project, admin_id)
    item = _resolve_with_action_item(db_session, topic, owner_id)

    client.post(
        f"/api/v1/action-items/{item['id']}/mark-wake-sent",
        headers=admin_headers,
    )
    client.post(
        f"/api/v1/action-items/{item['id']}/mark-stale",
        headers=admin_headers,
    )

    rows = _notifications_for(db_session, admin_id)
    stale_rows = [r for r in rows if r.event == "action_item.stale"]
    assert len(stale_rows) == 1
    assert stale_rows[0].category == NotificationCategory.wakeable
    payload = stale_rows[0].payload_json or {}
    assert payload.get("action_item_id") == item["id"]
    assert payload.get("topic_id") == str(topic.id)
    assert payload.get("wake_count") == 1
    assert payload.get("stale_at") is not None


# ---------------------------------------------------------------------------
# B-8: creator does NOT receive a wake notification (audit-only path)
# ---------------------------------------------------------------------------


def test_creator_does_not_receive_stale_notification(
    client, db_session, auth_headers, admin_headers, project
):
    """Plan §3 #3 (3c creator audit-only): the topic creator sees the
    ``action_item.stale`` event in the audit log but does NOT get a wakeable
    in-app notification. Only admins do.

    The test creates a topic via ``admin_headers`` (so admin is the topic
    creator) and verifies admin gets the stale notification but NOT through
    a creator-only path — the verification is that the notification is
    categorised as wakeable for admin, and that the topic creator (admin in
    this fixture) does not receive a separate creator-only notification.

    The policy is enforced by the recipient selector in
    ``notify_admin_action_item_stale`` — it queries ``Agent.role == admin``
    which is independent of ``topic.creator_agent_id``. This test pins the
    behaviour so a future refactor that adds a creator channel must break
    loudly.
    """
    owner_id = _owner_id_from_auth(client, auth_headers)
    admin_id = _admin_id_from_headers(client, admin_headers)
    topic = _create_topic(db_session, project, admin_id)
    assert str(topic.creator_agent_id) == admin_id  # admin created the topic

    item = _resolve_with_action_item(db_session, topic, owner_id)
    client.post(
        f"/api/v1/action-items/{item['id']}/mark-wake-sent",
        headers=admin_headers,
    )
    client.post(
        f"/api/v1/action-items/{item['id']}/mark-stale",
        headers=admin_headers,
    )

    admin_rows = _notifications_for(db_session, admin_id)
    admin_stale = [r for r in admin_rows if r.event == "action_item.stale"]
    assert len(admin_stale) == 1, (
        f"admin (as creator) should receive exactly one stale notification, "
        f"got {len(admin_stale)}"
    )
    # The payload is the admin-flavoured one — no creator-specific channel
    # marker. If a future refactor adds a creator channel it should be a new
    # event name (e.g. ``action_item.stale.creator``), not a duplicate of
    # the admin one.
    payload = admin_stale[0].payload_json or {}
    assert "kind" not in payload or payload.get("kind") == "action_item"


def test_owner_receives_wake_but_not_stale_notification(
    client, db_session, auth_headers, admin_headers, project
):
    """Owner should see wake notifications but NOT the stale notification
    (the stale path is admin-only — the owner already had 4 chances to
    respond and the diagnostic goes to admin as the stable收口)."""
    owner_id = _owner_id_from_auth(client, auth_headers)
    admin_id = _admin_id_from_headers(client, admin_headers)
    topic = _create_topic(db_session, project, admin_id)
    item = _resolve_with_action_item(db_session, topic, owner_id)

    client.post(
        f"/api/v1/action-items/{item['id']}/mark-wake-sent",
        headers=admin_headers,
    )
    client.post(
        f"/api/v1/action-items/{item['id']}/mark-stale",
        headers=admin_headers,
    )

    owner_events = _events_for(db_session, owner_id)
    assert "action_item.wake_sent" in owner_events
    assert "action_item.stale" not in owner_events, (
        f"owner should NOT receive stale notification, got {owner_events}"
    )


# ---------------------------------------------------------------------------
# B-5: stale is idempotent — second call does NOT emit a duplicate notification
# ---------------------------------------------------------------------------


def test_stale_idempotent_does_not_duplicate_notification(
    client, db_session, auth_headers, admin_headers, project
):
    owner_id = _owner_id_from_auth(client, auth_headers)
    admin_id = _admin_id_from_headers(client, admin_headers)
    topic = _create_topic(db_session, project, admin_id)
    item = _resolve_with_action_item(db_session, topic, owner_id)

    client.post(
        f"/api/v1/action-items/{item['id']}/mark-wake-sent",
        headers=admin_headers,
    )
    first = client.post(
        f"/api/v1/action-items/{item['id']}/mark-stale",
        headers=admin_headers,
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/v1/action-items/{item['id']}/mark-stale",
        headers=admin_headers,
    )
    assert second.status_code == 200

    admin_stale = [
        r for r in _notifications_for(db_session, admin_id)
        if r.event == "action_item.stale"
    ]
    # group_key dedup in notification_service prevents the second stale call
    # from spawning another row.
    assert len(admin_stale) == 1, (
        f"idempotent stale should not duplicate admin notification, "
        f"got {len(admin_stale)}"
    )


# ---------------------------------------------------------------------------
# Defensive: missing owner_agent_id (defence-in-depth, plan §3 + I3 contract)
# ---------------------------------------------------------------------------


def test_wake_notification_helper_skips_unassigned_items(db_session):
    """If somehow ``notify_owner_action_item_wake`` is called on an item
    without an owner (shouldn't happen — ``mark_wake_sent`` rejects
    unassigned items upstream), the helper returns [] rather than 500.
    """
    from server.domain.models import TopicActionItem
    from server.services.notification_service import notify_owner_action_item_wake

    fake_item = TopicActionItem(
        id=uuid.uuid4(),
        owner_agent_id=None,
        project_id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        title="orphan",
        wake_count=1,
    )
    # No assertions on DB side — helper should be a no-op for unassigned.
    result = notify_owner_action_item_wake(db_session, action_item=fake_item)
    assert result == []


# ---------------------------------------------------------------------------
# I6 / B-8: creator (regular agent, NOT admin) is audit-only on stale
# ---------------------------------------------------------------------------


def test_non_admin_creator_does_not_receive_stale_notification(
    client, db_session, auth_headers, admin_headers, project
):
    """Stronger form of B-8: when the topic creator is a regular agent
    (not an admin), they must NOT receive the stale notification. The audit
    log is their only channel — they can find the event via
    ``GET /api/v1/audit`` (B-11).

    Earlier ``test_creator_does_not_receive_stale_notification`` covers the
    case where the creator happens to be an admin (the admin path fires
    once for them, not via a creator-specific channel). This case is the
    sharper guardrail: if a future refactor adds a creator-only channel,
    this test must keep failing.
    """
    creator_id = uuid.UUID(_owner_id_from_auth(client, auth_headers))  # auth = creator
    admin_id = _admin_id_from_headers(client, admin_headers)

    # The topic is created via auth_headers (regular agent = creator).
    # Owner of the action_item is also auth_headers — separate the two
    # concerns by giving the action_item a different owner.
    other_owner_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={"name": "other-owner", "role": "agent", "project_key": project["project_key"]},
    )
    assert other_owner_resp.status_code == 201
    other_owner_id = uuid.UUID(other_owner_resp.json()["id"])

    topic = _create_topic(db_session, project, creator_id)  # auth = creator
    item = _resolve_with_action_item(db_session, topic, str(other_owner_id))

    client.post(
        f"/api/v1/action-items/{item['id']}/mark-wake-sent",
        headers=admin_headers,
    )
    resp = client.post(
        f"/api/v1/action-items/{item['id']}/mark-stale",
        headers=admin_headers,
    )
    assert resp.status_code == 200

    creator_events = _events_for(db_session, creator_id)
    assert "action_item.stale" not in creator_events, (
        f"non-admin creator must NOT receive stale notification, got {creator_events}"
    )
    # Sanity: admin did receive it (the only channel).
    admin_events = _events_for(db_session, admin_id)
    assert "action_item.stale" in admin_events
