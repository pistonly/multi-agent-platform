import json
import uuid
from queue import Empty

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


def test_enqueue_from_event_publishes_stream(client, auth_headers, reviewer, project):
    reviewer_id = uuid.UUID(reviewer["id"])
    queue = notification_stream.subscribe(reviewer_id)
    try:
        exp = client.post(
            f"/api/v1/projects/{project['id']}/experiments",
            headers=auth_headers,
            json={"title": "Stream Hook", "plan": {"content_md": "# p"}},
        ).json()
        while True:
            try:
                queue.get_nowait()
            except Empty:
                break
        client.post(f"/api/v1/experiments/{exp['id']}/submit-review", headers=auth_headers)

        payload = queue.get(timeout=2)
        data = json.loads(payload)
        assert data["type"] == "notification.created"
        assert data["event"] == "experiment.phase_changed"
    finally:
        notification_stream.unsubscribe(reviewer_id, queue)
