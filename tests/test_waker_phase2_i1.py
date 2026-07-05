"""Phase 2 I1 acceptance: emit_kind directs lifecycle SSE to persona agents.

Covers the 4 newly-wired publish points called out by plan v2 D2:

- topic_lifecycle       → topic.lifecycle.closed / reopened
- experiment_lifecycle  → experiment.lifecycle.cancelled / withdrawn
- addressed_review_item → review_item.status_changed

The other wake kinds (``pending_topic_reply`` / ``pending_review`` /
``pending_result_review`` / ``experiment_lifecycle`` for non-cancel
transitions) already publish via the existing ``emit()`` broadcast path;
the waker differentiates them by ``Notification.event`` in I2. The
assertions here focus on the *directed* SSE emit (per-persona recipient
set + Notification row with kind in payload).

To exercise "host + participant both receive" we trigger lifecycle
actions via the admin token: admin is the actor (and is NOT a persona
agent in the project), so the skip-actor branch in ``enqueue_for_agents``
leaves all persona recipients intact. Without this trick every assertion
would be off-by-one because the host is usually the actor.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow

from fastapi.testclient import TestClient


def _register_persona(client: TestClient, admin_headers: dict, project: dict, name: str) -> tuple[str, str]:
    res = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        params={"name": name, "role": "agent", "project_key": project["project_key"]},
    )
    assert res.status_code == 201, res.text
    data = res.json()
    return data["id"], data["api_token"]


def _capture_publish(monkeypatch) -> list[dict]:
    """Replace notification_stream.publish with a recorder so we can assert
    which recipients received which event without standing up an SSE client.
    """
    from server.services import notification_stream

    captured: list[dict] = []
    original = notification_stream.publish

    def spy(recipient_id, event):
        captured.append({"recipient_id": str(recipient_id), "event": event})
        original(recipient_id, event)

    monkeypatch.setattr(notification_stream, "publish", spy)
    return captured


def test_i1_close_topic_emits_kind_to_host_and_participant(
    monkeypatch, client, admin_headers, admin_token, project
) -> None:
    admin_id, admin_tok = admin_token
    _register_persona(client, admin_headers, project, "multi-agents-platform-host")
    _register_persona(client, admin_headers, project, "multi-agents-platform-participant")
    _register_persona(client, admin_headers, project, "multi-agents-platform-reviewer")
    admin_h = {"Authorization": f"Bearer {admin_tok}"}

    res = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=admin_h,
        json={"title": "I1 close", "description": ""},
    )
    assert res.status_code == 201, res.text
    topic_id = res.json()["id"]

    captured = _capture_publish(monkeypatch)
    res = client.post(f"/api/v1/topics/{topic_id}/close", headers=admin_h)
    assert res.status_code == 200, res.text

    closed = [c for c in captured if c["event"].get("event") == "topic.lifecycle.closed"]
    assert len(closed) == 2, (
        f"expected 2 recipients (host + participant), got {len(closed)}: {closed}"
    )
    assert admin_id not in {c["recipient_id"] for c in closed}, (
        "admin (actor) must be skipped; emit_kind → enqueue_for_agents → skip self"
    )


def test_i1_reopen_topic_emits_kind(
    monkeypatch, client, admin_headers, admin_token, project
) -> None:
    admin_id, admin_tok = admin_token
    _register_persona(client, admin_headers, project, "multi-agents-platform-host")
    _register_persona(client, admin_headers, project, "multi-agents-platform-participant")
    admin_h = {"Authorization": f"Bearer {admin_tok}"}

    res = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=admin_h,
        json={"title": "I1 reopen", "description": ""},
    )
    topic_id = res.json()["id"]
    client.post(f"/api/v1/topics/{topic_id}/close", headers=admin_h)

    captured = _capture_publish(monkeypatch)
    res = client.post(f"/api/v1/topics/{topic_id}/reopen", headers=admin_h)
    assert res.status_code == 200, res.text

    reopened = [c for c in captured if c["event"].get("event") == "topic.lifecycle.reopened"]
    assert len(reopened) == 2, f"expected 2 recipients, got {len(reopened)}"
    assert admin_id not in {c["recipient_id"] for c in reopened}


def test_i1_experiment_cancelled_emits_kind_to_host_and_reviewer(
    monkeypatch, client, admin_headers, admin_token, project
) -> None:
    admin_id, admin_tok = admin_token
    _register_persona(client, admin_headers, project, "multi-agents-platform-host")
    _register_persona(client, admin_headers, project, "multi-agents-platform-reviewer")
    admin_h = {"Authorization": f"Bearer {admin_tok}"}

    res = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=admin_h,
        json={"title": "I1 cancel", "topic_id": None, "plan": {"content_md": "p"}},
    )
    assert res.status_code == 201, res.text
    exp_id = res.json()["id"]

    captured = _capture_publish(monkeypatch)
    res = client.post(f"/api/v1/experiments/{exp_id}/cancel", headers=admin_h)
    assert res.status_code == 200, res.text

    cancelled = [
        c for c in captured if c["event"].get("event") == "experiment.lifecycle.cancelled"
    ]
    assert len(cancelled) == 2, (
        f"expected 2 recipients (host + reviewer), got {len(cancelled)}"
    )
    assert admin_id not in {c["recipient_id"] for c in cancelled}


def test_i1_experiment_withdrawn_emits_kind_to_host_and_reviewer(
    monkeypatch, client, admin_headers, admin_token, project
) -> None:
    admin_id, admin_tok = admin_token
    _register_persona(client, admin_headers, project, "multi-agents-platform-host")
    _register_persona(client, admin_headers, project, "multi-agents-platform-reviewer")
    admin_h = {"Authorization": f"Bearer {admin_tok}"}

    res = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=admin_h,
        json={"title": "I1 withdraw", "topic_id": None, "plan": {"content_md": "p"}},
    )
    exp_id = res.json()["id"]
    client.post(f"/api/v1/experiments/{exp_id}/submit-review", headers=admin_h)

    captured = _capture_publish(monkeypatch)
    res = client.post(f"/api/v1/experiments/{exp_id}/withdraw", headers=admin_h)
    assert res.status_code == 200, res.text

    withdrawn = [
        c for c in captured if c["event"].get("event") == "experiment.lifecycle.withdrawn"
    ]
    assert len(withdrawn) == 2, f"expected 2 recipients, got {len(withdrawn)}"
    assert admin_id not in {c["recipient_id"] for c in withdrawn}


def test_i1_review_item_status_changed_emits_kind_to_host(
    monkeypatch, client, admin_headers, admin_token, project
) -> None:
    # Reviewer changes the item status; host must be woken (skip-actor removes
    # reviewer, leaves host). Admin cannot transition review items, so the
    # reviewer persona drives this transition.
    _, admin_tok = admin_token
    host_id, _ = _register_persona(
        client, admin_headers, project, "multi-agents-platform-host"
    )
    _, reviewer_tok = _register_persona(
        client, admin_headers, project, "multi-agents-platform-reviewer"
    )
    admin_h = {"Authorization": f"Bearer {admin_tok}"}
    reviewer_h = {"Authorization": f"Bearer {reviewer_tok}"}

    res = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=admin_h,
        json={"title": "I1 review item", "topic_id": None, "plan": {"content_md": "p"}},
    )
    exp_id = res.json()["id"]
    client.post(f"/api/v1/experiments/{exp_id}/submit-review", headers=admin_h)
    # Reviewer persona creates the review → it becomes ``reviewer_agent_id``
    # so the same agent can later transition items as the reviewer.
    res = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer_h,
        json={"reasonable_items": ["r"], "unreasonable_items": ["need baseline"]},
    )
    assert res.status_code == 201, res.text
    review = res.json()
    item_id = next(i["id"] for i in review["items"] if i["kind"] == "unreasonable")

    captured = _capture_publish(monkeypatch)
    # Reviewer withdraws an unreasonable item (open → withdrawn, is_reviewer).
    # The addressed transition requires a plan revision, so we use a direct
    # reviewer-allowed transition here.
    res = client.patch(
        f"/api/v1/review-items/{item_id}", headers=reviewer_h, json={"status": "withdrawn"}
    )
    assert res.status_code == 200, res.text

    status = [c for c in captured if c["event"].get("event") == "review_item.status_changed"]
    assert len(status) == 1, f"expected 1 recipient (host), got {len(status)}"
    assert status[0]["recipient_id"] == host_id


def test_i1_emit_kind_skips_actor_for_self_close(
    monkeypatch, client, admin_headers, project
) -> None:
    """When the host closes its own topic, emit_kind must NOT wake the host
    (self-event). Only participant should be in the recipient set.
    """
    host_id, host_token = _register_persona(
        client, admin_headers, project, "multi-agents-platform-host"
    )
    _register_persona(client, admin_headers, project, "multi-agents-platform-participant")
    host_h = {"Authorization": f"Bearer {host_token}"}

    res = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=host_h,
        json={"title": "I1 self close", "description": ""},
    )
    topic_id = res.json()["id"]

    captured = _capture_publish(monkeypatch)
    client.post(f"/api/v1/topics/{topic_id}/close", headers=host_h)

    closed = [c for c in captured if c["event"].get("event") == "topic.lifecycle.closed"]
    recipient_ids = {c["recipient_id"] for c in closed}
    assert host_id not in recipient_ids, "host must be skipped when it triggers close"
    assert len(recipient_ids) == 1, (
        f"expected exactly 1 recipient (participant), got {len(recipient_ids)}: {recipient_ids}"
    )


def test_i1_notification_payload_carries_kind_and_ids(
    monkeypatch, client, admin_headers, admin_token, project, db_session
) -> None:
    """emit_kind inserts Notification rows with payload containing kind +
    topic_id so the waker can build kind-specific fingerprints in I2 without
    re-deriving from the SSE event name."""
    from server.domain.models import Notification

    _register_persona(client, admin_headers, project, "multi-agents-platform-host")
    _register_persona(client, admin_headers, project, "multi-agents-platform-participant")
    _, admin_tok = admin_token
    admin_h = {"Authorization": f"Bearer {admin_tok}"}

    res = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=admin_h,
        json={"title": "I1 payload", "description": ""},
    )
    topic_id = res.json()["id"]
    client.post(f"/api/v1/topics/{topic_id}/close", headers=admin_h)

    db_session.expire_all()
    rows = (
        db_session.query(Notification)
        .filter(Notification.event == "topic.lifecycle.closed")
        .all()
    )
    assert rows, "expected at least one Notification row for topic.lifecycle.closed"
    payload = rows[0].payload_json or {}
    assert payload.get("kind") == "topic.lifecycle", f"payload missing kind, got {payload}"
    assert payload.get("topic_id") == topic_id
    assert payload.get("status") == "closed"
