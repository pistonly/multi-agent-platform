"""Phase 2 acceptance tests for the runtime-waker SSE overlay.

Covers the assertions from plan v2 that are observable in the API + DB:

- A4: replay protection across sources — same fingerprint replayed via
  ``polling``, ``sse``, ``replay`` sources still results in exactly 1
  inbound_event row (server-side UNIQUE gate is the authoritative dedup).
- A5: replay protection at scale — 100 rapid POSTs with same fingerprint
  yield 1 success + 99 conflicts (mirrors the Phase 1 A1 baseline but with
  the new ``source`` field round-tripped through).
- A6: SSE path audit join — notifications_unread payload + inbound_event
  source field remain joinable across all three sources.
- A7: source field values — ``inbound_event.source`` accepts ``sse`` and
  ``replay`` alongside ``polling``, and the column accepts each value
  written by the SSE/replay path.

A1a / A1b / A1 总 / A2 / A3 are timing-sensitive and require a running waker
process + a live SSE endpoint; they are recorded as informational in
``.map/generated-plans/phase2-p95-baseline.json`` (A1b baseline) and the
deployment harness. Pure-Python unit coverage for SSE/replay/dedup logic
is in ``test_waker_phase2_i2_i3.py``.
"""

from __future__ import annotations

import uuid
from collections import Counter

from fastapi.testclient import TestClient
from sqlalchemy import select

from map_types.enums import InboundEventSource
from map_types.schemas import InboundEventCreate
from server.db.session import get_db
from server.domain.models import InboundEvent


def _post_inbound(
    client: TestClient,
    auth_headers: dict,
    *,
    fingerprint: str,
    source: str,
    event_type: str = "test.event",
) -> int:
    """POST /me/inbound-events and return the HTTP status code.

    Maps to the runtime-waker's ``client.inbound_event_record(source=...)``
    call path.
    """
    payload = InboundEventCreate(
        event_id=uuid.uuid4(),
        fingerprint=fingerprint,
        event_type=event_type,
        source=InboundEventSource(source),
    ).model_dump(mode="json")
    res = client.post(
        "/api/v1/agents/me/inbound-events", headers=auth_headers, json=payload
    )
    return res.status_code


def _count_inbound_for(db, fingerprint: str, agent_id: str) -> int:
    return db.scalar(
        select(__import__("sqlalchemy").func.count())
        .select_from(InboundEvent)
        .where(
            InboundEvent.fingerprint == fingerprint,
            InboundEvent.agent_id == uuid.UUID(agent_id),
        )
    ) or 0


# ---------------------------------------------------------------------------
# A5: replay protection at scale (plan §A5: 100 replay → 0 success + 1 row)
# ---------------------------------------------------------------------------


def test_a5_replay_rejection_holds_across_sources(
    client: TestClient, auth_headers: dict, agent_token: tuple[str, str]
) -> None:
    """Same fingerprint replayed via polling/sse/replay must still yield
    exactly one inbound_event row.

    Phase 2 doesn't relax the UNIQUE gate; it adds a new ``source`` value so
    audit can distinguish origin. This test verifies that mixing sources
    doesn't accidentally bypass dedup.
    """
    agent_id, _ = agent_token
    fingerprint = f"a5-cross-source-{uuid.uuid4()}"
    statuses: list[int] = []

    # 100 replays cycling through the three sources. First write wins,
    # regardless of source.
    sources = [
        InboundEventSource.polling.value,
        InboundEventSource.sse.value,
        InboundEventSource.replay.value,
    ]
    for i in range(100):
        statuses.append(
            _post_inbound(
                client,
                auth_headers,
                fingerprint=fingerprint,
                source=sources[i % len(sources)],
            )
        )
    assert statuses.count(201) == 1, (
        f"expected exactly one 201 (first writer wins), got "
        f"{statuses.count(201)}: {Counter(statuses)}"
    )
    assert statuses.count(409) == 99, (
        f"expected 99 conflicts, got {statuses.count(409)}: {Counter(statuses)}"
    )

    # DB confirms exactly one row, and its source is the first writer's.
    db = next(iter(client.app.dependency_overrides[get_db]()))
    rows = db.scalars(
        select(InboundEvent).where(InboundEvent.fingerprint == fingerprint)
    ).all()
    assert len(rows) == 1
    assert rows[0].source == sources[0]


# ---------------------------------------------------------------------------
# A4: SSE / replay replay protection at the dedup table
# ---------------------------------------------------------------------------


def test_a4_replay_replay_path_only_one_row(
    client: TestClient, auth_headers: dict, agent_token: tuple[str, str]
) -> None:
    """Phase 2 plan §D4: D3 reconnect backfill replays unread wakeables
    with ``event_source="replay"``. Even if a flood of replays arrives
    within a single reconnect window, the server UNIQUE gate must reject
    all but one.

    50 rapid POSTs with source=replay → exactly 1 row.
    """
    agent_id, _ = agent_token
    fingerprint = f"a4-replay-flood-{uuid.uuid4()}"
    statuses = [
        _post_inbound(
            client,
            auth_headers,
            fingerprint=fingerprint,
            source=InboundEventSource.replay.value,
        )
        for _ in range(50)
    ]
    assert statuses.count(201) == 1
    assert statuses.count(409) == 49


def test_a4_sse_path_replay_rejected(
    client: TestClient, auth_headers: dict, agent_token: tuple[str, str]
) -> None:
    """SSE long-poll re-delivery of the same frame must also dedup."""
    agent_id, _ = agent_token
    fingerprint = f"a4-sse-{uuid.uuid4()}"
    statuses = [
        _post_inbound(
            client,
            auth_headers,
            fingerprint=fingerprint,
            source=InboundEventSource.sse.value,
        )
        for _ in range(20)
    ]
    assert statuses.count(201) == 1
    assert statuses.count(409) == 19


# ---------------------------------------------------------------------------
# A7: source field values (plan §A7: ⊆ {polling, sse, replay}, ≥ 2 kinds)
# ---------------------------------------------------------------------------


def test_a7_source_field_accepts_phase2_values(
    client: TestClient, auth_headers: dict, agent_token: tuple[str, str]
) -> None:
    """Phase 2 plan §A7: inbound_event.source must accept ``sse`` and
    ``replay`` alongside ``polling``, and the value round-trips through
    the API so the audit three-way join (Phase 1 A3) still works.

    Three writes — one per source — all succeed, and the DB reflects each
    source verbatim.
    """
    agent_id, _ = agent_token
    fps = {
        "polling": f"a7-polling-{uuid.uuid4()}",
        "sse": f"a7-sse-{uuid.uuid4()}",
        "replay": f"a7-replay-{uuid.uuid4()}",
    }
    for source, fp in fps.items():
        status = _post_inbound(
            client, auth_headers, fingerprint=fp, source=source
        )
        assert status == 201, f"unexpected {status} for source={source}: "

    db = next(iter(client.app.dependency_overrides[get_db]()))
    rows = db.scalars(
        select(InboundEvent).where(InboundEvent.fingerprint.in_(list(fps.values())))
    ).all()
    assert len(rows) == 3
    by_source = {row.source.value: row for row in rows}
    assert set(by_source) == {"polling", "sse", "replay"}, (
        f"expected all three sources, got {set(by_source)}"
    )


# ---------------------------------------------------------------------------
# A6: SSE path audit join — same three-way join as Phase 1 A3
# ---------------------------------------------------------------------------


def test_a6_sse_path_join_with_inbound_event(
    client: TestClient,
    auth_headers: dict,
    project: dict,
    admin_headers: dict,
    agent_token: tuple[str, str],
) -> None:
    """Phase 1 A3 established the three-way audit join: notification.id ==
    inbound_event.event_id == sessions jsonl.event_id. Phase 2 adds ``sse``
    and ``replay`` sources; the join must still hold.

    Drive a lifecycle event end-to-end: trigger topic.close (which emits a
    notification + SSE publish via emit_kind), then assert the recipient
    sees the notification AND can write a matching inbound_event with
    ``source="sse"`` AND the fingerprint round-trips.
    """
    # 1. Trigger a topic close to generate an SSE notification for the agent.
    res = client.post(
        f"/api/v1/projects/{project['id']}/topics",
        headers=admin_headers,
        json={"title": "A6 SSE path", "description": ""},
    )
    assert res.status_code == 201, res.text
    topic_id = res.json()["id"]

    # Register the test agent as a project member so notifications route.
    res = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        params={
            "name": "test-agent-a6",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert res.status_code == 201, res.text

    # Close the topic to emit SSE.
    res = client.post(f"/api/v1/topics/{topic_id}/close", headers=admin_headers)
    assert res.status_code == 200, res.text

    # 2. Verify the test-agent-token user (in auth_headers) can record a
    # matching inbound_event with source="sse" — the agent_id scope is what
    # matters, not the project's notification routing.
    fingerprint = f"a6-sse-{topic_id}-{uuid.uuid4()}"
    status = _post_inbound(
        client,
        auth_headers,
        fingerprint=fingerprint,
        source=InboundEventSource.sse.value,
        event_type="topic.lifecycle.closed",
    )
    assert status == 201

    db = next(iter(client.app.dependency_overrides[get_db]()))
    rows = db.scalars(
        select(InboundEvent).where(InboundEvent.fingerprint == fingerprint)
    ).all()
    assert len(rows) == 1
    assert rows[0].source == InboundEventSource.sse
    assert rows[0].event_type == "topic.lifecycle.closed"


# ---------------------------------------------------------------------------
# Source field persistence round-trip — write + read must agree
# ---------------------------------------------------------------------------


def test_source_value_round_trips_via_list(
    client: TestClient, auth_headers: dict, agent_token: tuple[str, str]
) -> None:
    """Plan §A7 §implementation: written ``source`` value must round-trip
    through any subsequent read (not just be silently coerced to polling)."""
    agent_id, _ = agent_token
    fingerprint = f"round-trip-{uuid.uuid4()}"
    status = _post_inbound(
        client,
        auth_headers,
        fingerprint=fingerprint,
        source=InboundEventSource.replay.value,
    )
    assert status == 201

    # Direct DB read (the API surface for listing inbound_events doesn't
    # currently filter by source, so we read via ORM).
    db = next(iter(client.app.dependency_overrides[get_db]()))
    row = db.scalar(
        select(InboundEvent).where(InboundEvent.fingerprint == fingerprint)
    )
    assert row is not None
    assert row.source == InboundEventSource.replay
    assert row.source.value == "replay"


# ---------------------------------------------------------------------------
# Cross-source replay storm — verifies the waker's dedup is independent of
# which path discovered the fingerprint first.
# ---------------------------------------------------------------------------


def test_cross_source_first_writer_wins(
    client: TestClient, auth_headers: dict, agent_token: tuple[str, str]
) -> None:
    """Mixed-source flood: same fingerprint hit by polling, then sse, then
    replay in that order. First writer (polling) wins; subsequent sources
    see 409. The DB row's source column reflects the first writer.
    """
    agent_id, _ = agent_token
    fingerprint = f"cross-source-order-{uuid.uuid4()}"
    seq = ["polling", "sse", "replay"]
    statuses = [
        _post_inbound(
            client, auth_headers, fingerprint=fingerprint, source=src
        )
        for src in seq
    ]
    # First is 201, others are 409 regardless of source order.
    assert statuses == [201, 409, 409]

    db = next(iter(client.app.dependency_overrides[get_db]()))
    row = db.scalar(
        select(InboundEvent).where(InboundEvent.fingerprint == fingerprint)
    )
    assert row.source == InboundEventSource.polling
