import json
import uuid
from queue import Empty

import pytest

from server.services import notification_stream


def test_notification_stream_publish_subscribe():
    agent_id = uuid.uuid4()
    queue = notification_stream.subscribe(agent_id)
    try:
        notification_stream.publish(
            agent_id,
            {"type": "notification.created", "event": "test.event", "notification_id": "n1"},
        )
        payload = queue.get(timeout=1)
        data = json.loads(payload)
        assert data["type"] == "notification.created"
        assert data["event"] == "test.event"
    finally:
        notification_stream.unsubscribe(agent_id, queue)


def test_digest_enqueue_does_not_publish_sse_frame(
    client, auth_headers, reviewer, project
):
    """v0.9 (Round 1 §4 + §7.2): the SSE stream MUST NOT carry digest
    notifications — the waker would receive them, only to drop them at the
    ``category == wakeable`` filter. Suppressing digest at the publish gate
    saves bandwidth + keeps session logs clean of phantom wake attempts.
    """
    reviewer_id = uuid.UUID(reviewer["id"])
    queue = notification_stream.subscribe(reviewer_id)
    try:
        exp = client.post(
            f"/api/v1/projects/{project['id']}/experiments",
            headers=auth_headers,
            json={"title": "Digest SSE suppression", "plan": {"content_md": "# p"}},
        ).json()
        # Drain any frame from experiment creation.
        while True:
            try:
                queue.get_nowait()
            except Empty:
                break
        # experiment.phase_changed is a digest event by default.
        client.post(
            f"/api/v1/experiments/{exp['id']}/submit-review", headers=auth_headers
        )

        # No SSE frame should arrive for a digest event.
        with pytest.raises(Empty):
            queue.get(timeout=0.5)
    finally:
        notification_stream.unsubscribe(reviewer_id, queue)


def test_wakeable_enqueue_publishes_sse_frame_with_v2_fingerprint(
    client, auth_headers, reviewer, project, db_session
):
    """v0.9 Round 1 §4: wakeable SSE frames must carry the v2 fingerprint
    marker so the waker's resume gate accepts them. Pinned here because
    the SSE frame contract is what ``discover_wake_events`` consumes.

    Driven via the service layer (no HTTP) so we can target a single
    recipient deterministically — emit_kind fans out by persona name, and
    HTTP-driven lifecycle events target only host/participant.
    """
    from map_types.enums import NotificationCategory
    from server.services import notification_service

    reviewer_id = uuid.UUID(reviewer["id"])
    queue = notification_stream.subscribe(reviewer_id)
    try:
        notification_service.enqueue_for_agents(
            db_session,
            recipient_agent_ids=[reviewer_id],
            project_id=uuid.UUID(project["id"]),
            actor_id=uuid.uuid4(),  # different from recipient so exclude_actor=True skips nothing
            event="experiment.lifecycle.withdrawn",
            summary="withdrawn for sse test",
            target_type="experiment",
            target_id=uuid.uuid4(),
            payload=None,
            wakeable=True,
            exclude_actor=False,
        )

        payload = queue.get(timeout=2)
        data = json.loads(payload)
        assert data["type"] == "notification.created"
        assert data["event"] == "experiment.lifecycle.withdrawn"
        assert data["category"] == NotificationCategory.wakeable.value
        assert data["fingerprint_version"] == "v2"
    finally:
        notification_stream.unsubscribe(reviewer_id, queue)
