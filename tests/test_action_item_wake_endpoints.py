"""Experiment B / I4: HTTP endpoints ``mark-wake-sent`` / ``mark-stale``.

Plan §3 / I4: the runtime-waker CLI calls these endpoints to advance the
three-stage escalation timeline. They wrap ``action_item_service.mark_wake_sent``
/ ``mark_stale`` (the I3 helpers) behind the same access gates as
``complete`` / ``cancel``:

- ``mark-wake-sent`` → owner or admin (mirrors A1 close path)
- ``mark-stale`` → admin only (system escalation, not assignee-driven)

The audit-row invariants are owned by ``action_item_service`` and covered
by ``tests/test_action_item_wake_stale.py`` — this file focuses on the
HTTP-level concerns (access control, idempotency, payload echo).

Fixtures (tests/conftest.py):
- ``auth_headers`` → project-bound test agent (acts as the action_item owner)
- ``admin_headers`` → admin
- ``reviewer`` → second project-bound agent (non-owner, non-admin)
"""

from __future__ import annotations

import uuid


def _create_topic(client, headers, project):
    resp = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=headers,
        json={"title": "wake endpoint test"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _resolve_with_action_item(client, headers, topic_id, owner_agent_id):
    resp = client.post(
        f"/api/v1/topics/{topic_id}/resolve",
        headers=headers,
        json={
            "decision": "test wake endpoint",
            "action_items": [{"title": "wake me", "owner_agent_id": owner_agent_id}],
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["action_items"][0]


def _owner_id_from_auth(db_session_or_client, auth_headers):
    """Resolve the agent UUID for the auth_headers token via the /agents/me endpoint."""
    resp = db_session_or_client.get("/api/v1/agents/me", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# mark-wake-sent
# ---------------------------------------------------------------------------


def test_mark_wake_sent_owner_succeeds(client, auth_headers, admin_headers, project):
    owner_id = _owner_id_from_auth(client, auth_headers)
    topic = _create_topic(client, admin_headers, project)
    item = _resolve_with_action_item(client, admin_headers, topic["id"], owner_id)

    resp = client.post(
        f"/api/v1/action-items/{item['id']}/mark-wake-sent",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["wake_count"] == 1
    assert body["last_woken_at"] is not None
    assert body["stale_at"] is None


def test_mark_wake_sent_admin_succeeds_for_other_owner(client, admin_headers, project):
    owner_id = _owner_id_from_auth(client, admin_headers)
    topic = _create_topic(client, admin_headers, project)
    item = _resolve_with_action_item(client, admin_headers, topic["id"], owner_id)

    resp = client.post(
        f"/api/v1/action-items/{item['id']}/mark-wake-sent",
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["wake_count"] == 1


def test_mark_wake_sent_non_owner_rejected(client, auth_headers, admin_headers, reviewer, project):
    owner_id = _owner_id_from_auth(client, auth_headers)
    topic = _create_topic(client, admin_headers, project)
    item = _resolve_with_action_item(client, admin_headers, topic["id"], owner_id)

    # reviewer is neither owner nor admin → 403
    resp = client.post(
        f"/api/v1/action-items/{item['id']}/mark-wake-sent",
        headers=reviewer["headers"],
    )
    assert resp.status_code == 403, resp.text


def test_mark_wake_sent_increments_count(client, auth_headers, admin_headers, project):
    owner_id = _owner_id_from_auth(client, auth_headers)
    topic = _create_topic(client, admin_headers, project)
    item = _resolve_with_action_item(client, admin_headers, topic["id"], owner_id)

    for expected in (1, 2, 3):
        resp = client.post(
            f"/api/v1/action-items/{item['id']}/mark-wake-sent",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["wake_count"] == expected


def test_mark_wake_sent_rejects_closed_item(client, auth_headers, admin_headers, project):
    owner_id = _owner_id_from_auth(client, auth_headers)
    topic = _create_topic(client, admin_headers, project)
    item = _resolve_with_action_item(client, admin_headers, topic["id"], owner_id)

    # Close it
    closed = client.post(
        f"/api/v1/action-items/{item['id']}/complete",
        headers=auth_headers,
    )
    assert closed.status_code == 200

    # Now wake → service raises ValueError → wrapper converts to ConflictError → 409.
    resp = client.post(
        f"/api/v1/action-items/{item['id']}/mark-wake-sent",
        headers=auth_headers,
    )
    assert resp.status_code == 409, resp.text


def test_mark_wake_sent_404_for_missing(client, admin_headers):
    resp = client.post(
        f"/api/v1/action-items/{uuid.uuid4()}/mark-wake-sent",
        headers=admin_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# mark-stale
# ---------------------------------------------------------------------------


def test_mark_stale_admin_succeeds(client, auth_headers, admin_headers, project):
    owner_id = _owner_id_from_auth(client, auth_headers)
    topic = _create_topic(client, admin_headers, project)
    item = _resolve_with_action_item(client, admin_headers, topic["id"], owner_id)

    # Audit invariant: stale requires last_woken_at set. Drive one wake first.
    wake = client.post(
        f"/api/v1/action-items/{item['id']}/mark-wake-sent",
        headers=auth_headers,
    )
    assert wake.status_code == 200

    resp = client.post(
        f"/api/v1/action-items/{item['id']}/mark-stale",
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stale_at"] is not None


def test_mark_stale_owner_rejected(client, auth_headers, admin_headers, project):
    """Owner cannot mark their own item stale — escalation is system-driven,
    not assignee-driven (plan §3 + I4 design intent)."""
    owner_id = _owner_id_from_auth(client, auth_headers)
    topic = _create_topic(client, admin_headers, project)
    item = _resolve_with_action_item(client, admin_headers, topic["id"], owner_id)

    resp = client.post(
        f"/api/v1/action-items/{item['id']}/mark-stale",
        headers=auth_headers,
    )
    assert resp.status_code == 403, resp.text


def test_mark_stale_idempotent(client, admin_headers, project):
    """The I3 helper short-circuits if stale_at is already set — verify the
    HTTP endpoint does not double-write the audit row."""
    owner_id = _owner_id_from_auth(client, admin_headers)
    topic = _create_topic(client, admin_headers, project)
    item = _resolve_with_action_item(client, admin_headers, topic["id"], owner_id)

    # Wake first so stale's "last_woken_at required" invariant passes.
    client.post(
        f"/api/v1/action-items/{item['id']}/mark-wake-sent",
        headers=admin_headers,
    )

    first = client.post(
        f"/api/v1/action-items/{item['id']}/mark-stale",
        headers=admin_headers,
    )
    assert first.status_code == 200
    first_stale_at = first.json()["stale_at"]

    second = client.post(
        f"/api/v1/action-items/{item['id']}/mark-stale",
        headers=admin_headers,
    )
    # Second call is a no-op — returns 200 with the unchanged stale_at.
    assert second.status_code == 200
    assert second.json()["stale_at"] == first_stale_at


def test_mark_stale_404_for_missing(client, admin_headers):
    resp = client.post(
        f"/api/v1/action-items/{uuid.uuid4()}/mark-stale",
        headers=admin_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Schema echoes the new wake fields
# ---------------------------------------------------------------------------


def test_action_item_response_includes_wake_fields(client, admin_headers, project):
    owner_id = _owner_id_from_auth(client, admin_headers)
    topic = _create_topic(client, admin_headers, project)
    item = _resolve_with_action_item(client, admin_headers, topic["id"], owner_id)

    listing = client.get(
        f"/api/v1/projects/{project['id']}/action-items",
        headers=admin_headers,
        params={"status": "open"},
    )
    assert listing.status_code == 200
    match = next(row for row in listing.json() if row["id"] == item["id"])
    for field in ("wake_count", "first_open_at", "last_woken_at", "stale_at"):
        assert field in match, f"missing {field} in action_item response"


def test_todos_action_items_includes_wake_fields(client, admin_headers, project):
    owner_id = _owner_id_from_auth(client, admin_headers)
    topic = _create_topic(client, admin_headers, project)
    _resolve_with_action_item(client, admin_headers, topic["id"], owner_id)

    resp = client.get("/api/v1/agents/me/todos", headers=admin_headers)
    assert resp.status_code == 200
    items = resp.json().get("action_items") or []
    assert items, "expected at least one action_item in todos"
    for field in ("wake_count", "first_open_at", "last_woken_at", "stale_at"):
        assert field in items[0], f"missing {field} in todos action_items"
