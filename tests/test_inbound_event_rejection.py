"""M30A/M31 I2 acceptance — legacy v1 fingerprints go to ``rejection_count`` path.

Owner = host. Covers plan §I2 acceptance criteria (AC9 in the experiment
plan): a v1 fingerprint (``inbound:<event_id>``) sent to
``POST /me/inbound-events`` MUST

1. return 200 OK with ``status="rejected_v1"`` and the current
   ``rejection_count`` — NOT 409, so a legacy client retry loop doesn't
   indefinitely re-attempt;
2. persist or upsert an ``InboundEvent`` row so the audit table still
   records the sighting (plan §"三层审计 join");
3. NOT trigger a waker resume — the runtime-waker routes v1 fingerprints
   through a client-side pre-check before paying for the resume pipeline.

v2 fingerprints continue to behave exactly as Phase 1 / D6 specified
(201 first sighting, 409 on replay, ``rejection_count`` stays 0).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from cli.runtime_waker import _is_legacy_v1_fingerprint
from map_types.enums import InboundEventSource
from map_types.schemas import InboundEventCreate
from server.domain.models import Agent, AgentRole, InboundEvent, Project
from server.services import inbound_event_service


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
    """The waker-side helper mirrors ``notification_service.is_legacy_v1_fingerprint``.

    Keeping the two implementations in sync is critical — the waker pre-checks
    before paying for the resume pipeline. If they drift, v2 fingerprints
    could silently route through the rejection_count path and never resume.
    """
    assert _is_legacy_v1_fingerprint("inbound:00000000-0000-0000-0000-000000000001") is True
    assert _is_legacy_v1_fingerprint("host:notification:abc:1") is False
    assert _is_legacy_v1_fingerprint("participant:action_items:xyz:1") is False
    assert _is_legacy_v1_fingerprint("host:pending_topic_replies:xyz:1") is False
    assert _is_legacy_v1_fingerprint("") is False


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


# ---------------------------------------------------------------------------
# runtime_waker: v1 fingerprint short-circuits before resume pipeline
# ---------------------------------------------------------------------------


@dataclass
class _RecordingBackend:
    """Captures every ``wake`` / ``wake_async`` invocation so the test can
    assert the waker never called them when a v1 fingerprint arrives.
    """

    wake_calls: list[dict[str, Any]] = field(default_factory=list)
    wake_async_calls: list[dict[str, Any]] = field(default_factory=list)

    def wake(self, *, persona: str, prompt: str, session_id: str | None = None) -> Any:  # type: ignore[no-untyped-def]
        from cli.runtime_waker import WakeResult

        self.wake_calls.append(
            {"persona": persona, "prompt": prompt, "session_id": session_id}
        )
        return WakeResult(session_id=session_id)

    async def wake_async(
        self,
        *,
        prompt: str,
        event_id: str | None = None,
        event_source: str = "polling",
        fingerprint: str | None = None,
    ) -> Any:
        from cli.runtime_waker import WakeResult

        self.wake_async_calls.append(
            {
                "prompt": prompt,
                "event_id": event_id,
                "event_source": event_source,
                "fingerprint": fingerprint,
            }
        )
        return WakeResult(session_id=None)


class _StubMapCommandClient:
    """Stub replacing :class:`cli.map_command_client.MapCommandClient` so the
    waker's ``_wake_event`` can call ``inbound_event_record`` without a real
    subprocess. We also capture the call list to assert the audit path fired.
    """

    def __init__(self) -> None:
        self.recorded: list[dict[str, Any]] = []

    def inbound_event_record(
        self,
        *,
        event_id: str,
        fingerprint: str,
        event_type: str,
        source: str = "polling",
    ) -> bool:
        self.recorded.append(
            {
                "event_id": event_id,
                "fingerprint": fingerprint,
                "event_type": event_type,
                "source": source,
            }
        )
        # 200 OK on the server side; CLI exits 0 → MapCommandClient returns True.
        return True


def _build_waker(
    tmp_path,
    backend: _RecordingBackend,
    client: _StubMapCommandClient,
) -> Any:
    """Build a minimal RuntimeWaker pointed at the stubs.

    We deliberately avoid the real ``_resolve_api_url`` /
    ``_resolve_bearer_token`` helpers — those need ``.map/config.yaml`` and
    aren't exercised by the v1 pre-check we're testing here.
    """
    from cli.runtime_waker import RuntimeWaker, RuntimeWakerConfig

    cfg = RuntimeWakerConfig(
        project_root=tmp_path,
        persona="host",
        interval=0,
        cooldown_seconds=0,
        sse_recent_resume_window_seconds=0,
        sse_enabled=False,
        state_file=None,
    )
    waker = RuntimeWaker(client=client, config=cfg, backend=backend)
    return waker


def test_waker_v1_fingerprint_skips_resume_and_marks_v1_rejected(tmp_path) -> None:
    """A v1 fingerprint MUST NOT call ``backend.wake*``.

    The pre-check routes the call through ``inbound_event_record`` (so the
    audit row exists and ``rejection_count`` increments server-side), then
    marks the event ``v1_rejected`` and returns. The resume pipeline is
    never entered.
    """
    backend = _RecordingBackend()
    client = _StubMapCommandClient()
    waker = _build_waker(tmp_path, backend, client)

    event = _make_wake_event(fingerprint=f"inbound:{uuid.uuid4()}")
    asyncio.run(waker._wake_event(event))  # noqa: SLF001 — test reaches into private API

    assert backend.wake_calls == [], "v1 path must NOT call backend.wake"
    assert backend.wake_async_calls == [], "v1 path must NOT call backend.wake_async"
    assert len(client.recorded) == 1
    assert client.recorded[0]["fingerprint"] == event.fingerprint

    state = waker._event_state(event)  # noqa: SLF001
    assert state.get("status") == "v1_rejected"


def test_waker_v2_fingerprint_proceeds_to_resume(tmp_path) -> None:
    """A v2 fingerprint MUST still go through the resume pipeline (Phase 1 invariant)."""
    backend = _RecordingBackend()
    client = _StubMapCommandClient()
    waker = _build_waker(tmp_path, backend, client)

    event = _make_wake_event(
        fingerprint=f"host:notification:{uuid.uuid4()}:1",
        persona="host",
    )
    asyncio.run(waker._wake_event(event))  # noqa: SLF001

    # PersonaAgentWakeBackend is what we'd get in production but our stub is
    # the raw backend, so we expect ``wake_calls`` (sync path). The point of
    # the assertion is the call count: exactly one wake attempt fires.
    total_wakes = len(backend.wake_calls) + len(backend.wake_async_calls)
    assert total_wakes == 1, "v2 fingerprint must proceed to resume exactly once"
    assert len(client.recorded) == 1
    assert client.recorded[0]["fingerprint"] == event.fingerprint


def _make_wake_event(
    *,
    fingerprint: str,
    persona: str = "host",
) -> Any:
    """Build a minimal :class:`WakeEvent` for the waker pre-check tests."""
    from cli.runtime_waker import WakeEvent

    return WakeEvent(
        persona=persona,
        kind="pending_topic_reply",
        fingerprint=fingerprint,
        object_id="test-object-id",
        title="test",
    )
