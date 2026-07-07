"""M30A/M31 I2 acceptance — legacy v1 fingerprints go to ``rejection_count`` path.

Owner = host. Covers plan §I2 acceptance criteria (AC9 in the experiment
plan): a v1 fingerprint (``inbound:<event_id>``) sent to
``POST /me/inbound-events`` MUST

1. return 200 OK with ``status="rejected_v1"`` and the current
   ``rejection_count`` — NOT 409, so a legacy client retry loop doesn't
   indefinitely re-attempt;
2. persist or upsert an ``InboundEvent`` row so the audit table still
   records the sighting (plan §"三层审计 join");
3. NOT trigger a waker resume — the host's resume endpoint routes v1
   fingerprints through the ``rejection_count`` path instead of resuming.

v2 fingerprints continue to behave exactly as Phase 1 / D6 specified
(201 first sighting, 409 on replay, ``rejection_count`` stays 0).

The legacy runtime-waker integration tests (which asserted the waker's
client-side pre-check) were removed when ``cli/runtime_waker.py`` was
retired; the server-side ``is_legacy_v1_fingerprint`` gate is now the
sole authority and is covered by the service/API tests below.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from map_types.enums import InboundEventSource
from map_types.schemas import InboundEventCreate
from sqlalchemy import select

from server.domain.models import Agent, AgentRole, InboundEvent, Project
from server.services import inbound_event_service
from server.services.notification_service import is_legacy_v1_fingerprint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(
    db_session,
    *,
    name: str,
    project_id: uuid.UUID,
    role: AgentRole = AgentRole.agent,
) -> Agent:
    agent = Agent(
        id=uuid.uuid4(),
        name=f"{name}-{uuid.uuid4().hex[:8]}",
        api_token_hash="x" * 64,
        api_token_prefix="test",
        project_id=project_id,
        role=role,
    )
    db_session.add(agent)
    db_session.flush()
    return agent


def _make_project(db_session) -> Project:
    project = Project(
        id=uuid.uuid4(),
        key=f"p-{uuid.uuid4().hex[:8]}",
        name="rejection-test",
        workspace_path="/tmp",
    )
    db_session.add(project)
    db_session.flush()
    return project


def _post_inbound(
    client: TestClient,
    headers: dict[str, str],
    *,
    fingerprint: str,
    event_id: uuid.UUID | None = None,
    event_type: str = "pending_topic_reply",
    source: str = "polling",
) -> Any:
    payload = InboundEventCreate(
        event_id=event_id or uuid.uuid4(),
        event_type=event_type,
        source=InboundEventSource(source),
        fingerprint=fingerprint,
    ).model_dump(mode="json")
    return client.post("/api/v1/agents/me/inbound-events", headers=headers, json=payload)


# ---------------------------------------------------------------------------
# Pure-helper sanity
# ---------------------------------------------------------------------------


def test_is_legacy_v1_fingerprint_recognises_legacy_shape() -> None:
    """``notification_service.is_legacy_v1_fingerprint`` is the single authority.

    The host's resume endpoint uses this gate to route pre-v0.9
    ``inbound:<event_id>`` fingerprints to the ``rejection_count`` path
    instead of resuming a session. v0.9+ fingerprints are namespace-prefixed
    so they never start with ``inbound:``.
    """
    assert is_legacy_v1_fingerprint("inbound:00000000-0000-0000-0000-000000000001") is True
    assert is_legacy_v1_fingerprint("host:notification:abc:1") is False
    assert is_legacy_v1_fingerprint("participant:action_items:xyz:1") is False
    assert is_legacy_v1_fingerprint("host:pending_topic_replies:xyz:1") is False
    assert is_legacy_v1_fingerprint("") is False


# ---------------------------------------------------------------------------
# Service-level: v1 path persists/upserts and bumps rejection_count
# ---------------------------------------------------------------------------


def test_service_first_v1_returns_rejected_v1_with_count_1(
    db_session, auth_headers: dict[str, str]
) -> None:
    """First-time v1: row is INSERTED with ``rejection_count=1``."""
    project = db_session.scalar(select(Project).limit(1))
    assert project is not None
    agent = _make_agent(db_session, name="v1-first", project_id=project.id)

    fingerprint = f"inbound:{uuid.uuid4()}"
    payload = InboundEventCreate(
        event_id=uuid.uuid4(),
        event_type="pending_topic_reply",
        source=InboundEventSource.polling,
        fingerprint=fingerprint,
    )

    event, status = inbound_event_service.record_inbound_event(db_session, agent, payload)
    db_session.expire_all()
    db_session.refresh(event)

    assert status == "rejected_v1"
    assert event.rejection_count == 1
    assert event.fingerprint == fingerprint


def test_service_repeat_v1_bumps_rejection_count(
    db_session, auth_headers: dict[str, str]
) -> None:
    """Repeat v1: existing row found, ``rejection_count`` increments."""
    project = db_session.scalar(select(Project).limit(1))
    assert project is not None
    agent = _make_agent(db_session, name="v1-repeat", project_id=project.id)

    fingerprint = f"inbound:{uuid.uuid4()}"
    payload = InboundEventCreate(
        event_id=uuid.uuid4(),
        event_type="pending_topic_reply",
        source=InboundEventSource.polling,
        fingerprint=fingerprint,
    )

    _, status1 = inbound_event_service.record_inbound_event(db_session, agent, payload)
    _, status2 = inbound_event_service.record_inbound_event(db_session, agent, payload)
    _, status3 = inbound_event_service.record_inbound_event(db_session, agent, payload)
    db_session.expire_all()
    rows = db_session.scalars(
        select(InboundEvent).where(InboundEvent.fingerprint == fingerprint)
    ).all()

    assert status1 == "rejected_v1"
    assert status2 == "rejected_v1"
    assert status3 == "rejected_v1"
    assert len(rows) == 1, "v1 path must upsert, never duplicate the row"
    assert rows[0].rejection_count == 3


def test_service_v2_first_sighting_returns_recorded_with_zero_rejection(
    db_session, auth_headers: dict[str, str]
) -> None:
    """v2 fingerprint: stays on the Phase 1 D6 path (no rejection_count)."""
    project = db_session.scalar(select(Project).limit(1))
    assert project is not None
    agent = _make_agent(db_session, name="v2-first", project_id=project.id)

    payload = InboundEventCreate(
        event_id=uuid.uuid4(),
        event_type="pending_topic_reply",
        source=InboundEventSource.polling,
        fingerprint=f"host:notification:{uuid.uuid4()}:1",
    )

    event, status = inbound_event_service.record_inbound_event(db_session, agent, payload)
    db_session.expire_all()
    db_session.refresh(event)

    assert status == "recorded"
    assert event.rejection_count == 0


def test_service_v2_replay_returns_duplicate(
    db_session, auth_headers: dict[str, str]
) -> None:
    """v2 replay still goes through the Phase 1 D6 409 path."""
    project = db_session.scalar(select(Project).limit(1))
    assert project is not None
    agent = _make_agent(db_session, name="v2-replay", project_id=project.id)

    fingerprint = f"host:notification:{uuid.uuid4()}:1"
    payload = InboundEventCreate(
        event_id=uuid.uuid4(),
        event_type="pending_topic_reply",
        source=InboundEventSource.polling,
        fingerprint=fingerprint,
    )

    _, status1 = inbound_event_service.record_inbound_event(db_session, agent, payload)
    _, status2 = inbound_event_service.record_inbound_event(db_session, agent, payload)

    assert status1 == "recorded"
    assert status2 == "duplicate"


# ---------------------------------------------------------------------------
# API-level: status codes 200 (v1) / 201 (v2) / 409 (v2 replay)
# ---------------------------------------------------------------------------


def test_api_v1_returns_200_with_rejected_v1_status(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    fingerprint = f"inbound:{uuid.uuid4()}"
    res = _post_inbound(client, auth_headers, fingerprint=fingerprint)

    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "rejected_v1"
    assert body["event"]["fingerprint"] == fingerprint
    assert body["event"]["rejection_count"] == 1


def test_api_v1_repeat_increments_rejection_count(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    fingerprint = f"inbound:{uuid.uuid4()}"

    res1 = _post_inbound(client, auth_headers, fingerprint=fingerprint)
    res2 = _post_inbound(client, auth_headers, fingerprint=fingerprint)
    res3 = _post_inbound(client, auth_headers, fingerprint=fingerprint)

    assert res1.status_code == 200
    assert res2.status_code == 200
    assert res3.status_code == 200
    assert res1.json()["event"]["rejection_count"] == 1
    assert res2.json()["event"]["rejection_count"] == 2
    assert res3.json()["event"]["rejection_count"] == 3


def test_api_v1_and_v2_are_independent_rows(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """v1 and v2 fingerprints both create rows; they don't interfere."""
    v1 = f"inbound:{uuid.uuid4()}"
    v2 = f"host:notification:{uuid.uuid4()}:1"

    r_v1 = _post_inbound(client, auth_headers, fingerprint=v1)
    r_v2 = _post_inbound(client, auth_headers, fingerprint=v2)

    assert r_v1.status_code == 200
    assert r_v2.status_code == 201
    assert r_v1.json()["status"] == "rejected_v1"
    assert r_v2.json()["status"] == "recorded"


def test_api_v2_first_sighting_returns_201(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    res = _post_inbound(
        client,
        auth_headers,
        fingerprint=f"host:notification:{uuid.uuid4()}:1",
    )

    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "recorded"
    assert body["event"]["rejection_count"] == 0


def test_api_v2_replay_returns_409(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    fingerprint = f"host:notification:{uuid.uuid4()}:1"

    res1 = _post_inbound(client, auth_headers, fingerprint=fingerprint)
    res2 = _post_inbound(client, auth_headers, fingerprint=fingerprint)

    assert res1.status_code == 201
    assert res2.status_code == 409, res2.text
    assert "already exists" in res2.json()["detail"]
