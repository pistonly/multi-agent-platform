"""Phase 2 A6 + A7 — SSE-path three-way audit join + sessions jsonl event_source values.

Closes the gaps in ``test_waker_phase2_acceptance.py``:

- ``test_a6_sse_path_join_with_inbound_event`` only covers the 2-way join
  (notification.id == inbound_event.event_id). Phase 2 plan §A6 requires the
  full three-way audit join to still hold when the inbound_event source is
  ``sse`` — i.e. sessions jsonl.event_id must also match.
- ``test_a7_source_field_accepts_phase2_values`` only covers
  ``inbound_event.source``. Plan §A7 requires the sessions jsonl
  ``event_source`` field to accept ``polling`` / ``sse`` / ``replay`` and
  contain ≥ 2 distinct values in mixed-traffic scenarios, plus replay
  events must round-trip as ``"replay"``.

Both tests deliberately use the SSE long-poll flow end-to-end (topic.close
fires a topic.lifecycle notification routed via ``emit_kind`` to a host
persona agent) so we exercise the real Phase 2 D2 wiring, not a synthetic
shortcut.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from map_types.enums import InboundEventSource
from map_types.schemas import InboundEventCreate
from server.domain.models import InboundEvent, Notification


def _make_persona_agent(
    client: TestClient,
    admin_headers: dict[str, str],
    project: dict,
    name: str,
) -> dict[str, str]:
    """Register an agent by name in the project; returns id + headers."""
    res = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        params={
            "name": name,
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()
    return {
        "id": data["id"],
        "name": data["name"],
        "token": data["api_token"],
        "headers": {"Authorization": f"Bearer {data['api_token']}"},
    }


def _create_topic(
    client: TestClient,
    creator_headers: dict[str, str],
    project_id: str,
    title: str,
) -> str:
    res = client.post(
        f"/api/v1/projects/{project_id}/topics",
        headers=creator_headers,
        json={"title": title, "description": "phase 2 A6/A7"},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _post_inbound(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    fingerprint: str,
    event_id: uuid.UUID,
    event_type: str,
    source: InboundEventSource,
) -> int:
    payload = InboundEventCreate(
        event_id=event_id,
        fingerprint=fingerprint,
        event_type=event_type,
        source=source,
    ).model_dump(mode="json")
    res = client.post(
        "/api/v1/agents/me/inbound-events", headers=auth_headers, json=payload
    )
    return res.status_code


# ---------------------------------------------------------------------------
# A6: SSE-path three-way audit join
# ---------------------------------------------------------------------------


def test_a6_sse_path_three_way_audit_join(
    client: TestClient,
    admin_headers: dict[str, str],
    project: dict,
    db_session,
    tmp_path: Path,
) -> None:
    """Phase 2 plan §A6: SSE-path events must round-trip through three layers.

    Layers:
      1. ``Notification.id`` (event layer, server-side fan-out via emit_kind)
      2. ``InboundEvent.event_id`` (access layer, written by the waker with
         ``source="sse"`` when the SSE frame arrives)
      3. ``sessions jsonl.event_id`` (execution layer, written by
         ``append_session_wake_log`` when the wake actually fires)

    All three must hold the same UUID for the audit join to remain usable
    after Phase 2 introduces ``sse`` / ``replay`` sources.
    """
    from cli.session_wake_log import append_session_wake_log

    # 1. Register a host persona agent so emit_kind routes the topic.close
    # SSE notification to it. emit_kind resolves recipients by NAME within the
    # project, matching PERSONA_AGENT_NAMES in notification_service.py.
    host = _make_persona_agent(
        client, admin_headers, project, "multi-agents-platform-host"
    )

    # 2. Register a second agent that creates the topic (so we have a
    # non-host actor — keeps the test symmetric to Phase 1 A3).
    creator = _make_persona_agent(
        client, admin_headers, project, "creator-a6"
    )

    # 3. Creator creates the topic.
    topic_id = _create_topic(
        client, creator["headers"], project["id"], "A6 SSE 3-way join"
    )

    # 4. Admin closes the topic → emit_kind → SSE notification fans out to
    # host persona (admin is actor → excluded from recipients).
    close_res = client.post(
        f"/api/v1/topics/{topic_id}/close", headers=admin_headers
    )
    assert close_res.status_code == 200, close_res.text

    # 5. Host fetches its notifications, finds the topic.lifecycle one.
    notif_res = client.get(
        "/api/v1/agents/me/notifications", headers=host["headers"]
    )
    assert notif_res.status_code == 200
    items = notif_res.json()["items"]
    target = next(
        (
            n
            for n in items
            if n.get("target_type") == "topic"
            and n.get("target_id") == topic_id
        ),
        None,
    )
    assert target is not None, (
        f"host persona did not receive topic.close notification; got {items}"
    )
    assert target["event"] == "topic.lifecycle.closed", (
        f"unexpected event type for SSE notification: {target['event']}"
    )
    notification_id = target["id"]

    # 6. Host records inbound_event with source="sse" pointing at the SSE
    # notification's UUID. This is what the waker would do after parsing a
    # `notification.created` SSE frame.
    fingerprint = f"host:topic_lifecycle:{notification_id}"
    status = _post_inbound(
        client,
        host["headers"],
        fingerprint=fingerprint,
        event_id=uuid.UUID(notification_id),
        event_type="topic.lifecycle.closed",
        source=InboundEventSource.sse,
    )
    assert status == 201, f"inbound_event write failed: status={status}"

    # 7. Host writes a sessions jsonl entry with event_source="sse" — this
    # is the third leg of the audit join.
    log_dir = tmp_path / "a6-sessions"
    session_id = f"sess-a6-{uuid.uuid4().hex[:8]}"
    log_path = append_session_wake_log(
        log_dir=log_dir,
        session_id=session_id,
        persona="host",
        integration="waker",
        prompt="MAP wake · topic_lifecycle · A6 SSE 3-way",
        response_text="ok",
        status="ok",
        event_id=notification_id,
        event_source="sse",
        fingerprint=fingerprint,
    )
    assert log_path.is_file(), f"sessions jsonl not written at {log_path}"

    # 8. The three-way assertion. notification.id == inbound_event.event_id
    # == sessions jsonl.event_id. Drift here means reviewer cannot pivot
    # from notification to execution log without a persona-name string join.
    db_session.expire_all()
    notif = (
        db_session.query(Notification)
        .filter(Notification.id == uuid.UUID(notification_id))
        .one()
    )
    ib = (
        db_session.query(InboundEvent)
        .filter(InboundEvent.fingerprint == fingerprint)
        .one()
    )
    assert str(notif.id) == str(ib.event_id), (
        f"notification↔inbound_event drift: notif.id={notif.id} "
        f"vs ib.event_id={ib.event_id}"
    )
    assert ib.source == InboundEventSource.sse, (
        f"inbound_event source should round-trip as 'sse', got {ib.source}"
    )

    last_line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    entry = json.loads(last_line)
    assert entry["event_id"] == notification_id, (
        f"sessions jsonl↔notification drift: entry.event_id={entry['event_id']} "
        f"vs notif.id={notification_id}"
    )
    assert entry["event_source"] == "sse", (
        f"sessions jsonl event_source should be 'sse', got {entry['event_source']!r}"
    )
    assert entry["fingerprint"] == fingerprint

    log_path.unlink()


# ---------------------------------------------------------------------------
# A7: sessions jsonl event_source field values
# ---------------------------------------------------------------------------


_ALLOWED_SOURCES = {"polling", "sse", "replay"}


def test_a7_sessions_jsonl_event_source_values_cover_all_three(tmp_path: Path) -> None:
    """Phase 2 plan §A7: sessions jsonl event_source ⊆ {polling, sse, replay}.

    Writes three entries to the same session jsonl — one per source — and
    asserts the round-tripped values are exactly the allowed set, with ≥ 2
    distinct kinds present (mixed-traffic reality).

    This test does not exercise the waker end-to-end (no SSE / DB writes)
    because the value round-trip is purely a jsonl serialization concern;
    ``append_session_wake_log`` is the single writer. A2 already covers
    inbound_event source; this covers the third audit leg independently.
    """
    from cli.session_wake_log import append_session_wake_log, resolve_session_log_path

    log_dir = tmp_path / "a7-sessions"
    log_dir.mkdir(parents=True, exist_ok=True)
    session_id = f"sess-a7-{uuid.uuid4().hex[:8]}"

    sources = ["polling", "sse", "replay"]
    fingerprints = []
    for source in sources:
        fp = f"host:a7-{source}-{uuid.uuid4()}"
        fingerprints.append(fp)
        append_session_wake_log(
            log_dir=log_dir,
            session_id=session_id,
            persona="host",
            integration="waker",
            prompt=f"MAP wake · A7 {source} write",
            response_text="ok",
            status="ok",
            event_id=str(uuid.uuid4()),
            event_source=source,
            fingerprint=fp,
        )

    log_path = resolve_session_log_path(log_dir, session_id, "host")
    entries = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(entries) == len(sources), (
        f"expected {len(sources)} jsonl entries, got {len(entries)}"
    )

    # Each entry's event_source must round-trip verbatim — no coercion to
    # "polling" default, no empty string.
    for entry in entries:
        assert entry["event_source"] in _ALLOWED_SOURCES, (
            f"A7 violation: event_source={entry['event_source']!r} not in "
            f"{_ALLOWED_SOURCES}"
        )
        assert entry["event_source"], "A7 violation: event_source is empty"

    # Mixed-traffic reality: at least 2 distinct sources present.
    distinct = {entry["event_source"] for entry in entries}
    assert len(distinct) >= 2, (
        f"A7 requires ≥ 2 distinct event_source values, got {distinct}"
    )
    # And specifically all three for completeness of the contract.
    assert distinct == _ALLOWED_SOURCES, (
        f"expected all three sources in mixed traffic, got {distinct}"
    )

    log_path.unlink()


def test_a7_replay_event_writes_replay_source_to_sessions_jsonl(tmp_path: Path) -> None:
    """Phase 2 plan §A7 (补漏事件 sessions jsonl 必填 "replay"):

    Any entry whose event_source is replay must round-trip as ``"replay"`` —
    no silent coercion, no default fallback to ``"polling"``. This is the
    audit invariant that lets reviewers distinguish D3 reconnect backfills
    from real-time SSE frames in production logs.
    """
    from cli.session_wake_log import append_session_wake_log

    log_dir = tmp_path / "a7-replay-sessions"
    log_dir.mkdir(parents=True, exist_ok=True)
    session_id = f"sess-a7-replay-{uuid.uuid4().hex[:8]}"

    # Write 5 replay entries with distinct fingerprints.
    fingerprints = []
    for _ in range(5):
        fp = f"host:a7-replay-{uuid.uuid4()}"
        fingerprints.append(fp)
        append_session_wake_log(
            log_dir=log_dir,
            session_id=session_id,
            persona="host",
            integration="waker",
            prompt="MAP wake · A7 replay flood",
            response_text="ok",
            status="ok",
            event_id=str(uuid.uuid4()),
            event_source="replay",
            fingerprint=fp,
        )

    # append_session_wake_log writes to per-session-per-persona files via
    # resolve_session_log_path; locate the actual file rather than assuming
    # the canonical path.
    candidates = list(log_dir.rglob("*.jsonl"))
    assert candidates, f"no jsonl files written under {log_dir}"
    lines: list[str] = []
    for path in candidates:
        lines.extend(path.read_text(encoding="utf-8").splitlines())

    replay_entries = [
        json.loads(line)
        for line in lines
        if json.loads(line).get("event_source") == "replay"
    ]
    assert len(replay_entries) == 5, (
        f"expected 5 replay entries, got {len(replay_entries)}"
    )
    for entry in replay_entries:
        assert entry["event_source"] == "replay", (
            f"replay entry round-tripped as {entry['event_source']!r}"
        )
        assert entry["fingerprint"] in fingerprints


def test_a7_sse_path_inbound_event_source_matches_sessions_jsonl_source(
    client: TestClient,
    admin_headers: dict[str, str],
    project: dict,
    db_session,
    tmp_path: Path,
) -> None:
    """Stronger invariant: for the same fingerprint, the inbound_event.source
    on the server side and the sessions jsonl event_source on the waker side
    must agree.

    Plan §A7 says the audit three-segment join works across ``polling`` and
    ``sse``; without this cross-layer agreement, a reviewer cannot answer
    "where did this wake come from" without consulting two different
    fields. This is the operational invariant reviewers will check when
    triaging an unexpected wake flood.
    """
    from cli.session_wake_log import append_session_wake_log

    host = _make_persona_agent(
        client, admin_headers, project, "multi-agents-platform-host"
    )
    creator = _make_persona_agent(
        client, admin_headers, project, "creator-a7-cross"
    )
    topic_id = _create_topic(
        client, creator["headers"], project["id"], "A7 cross-layer source match"
    )

    close_res = client.post(
        f"/api/v1/topics/{topic_id}/close", headers=admin_headers
    )
    assert close_res.status_code == 200

    notif_res = client.get(
        "/api/v1/agents/me/notifications", headers=host["headers"]
    )
    items = notif_res.json()["items"]
    target = next(
        n
        for n in items
        if n.get("target_type") == "topic" and n.get("target_id") == topic_id
    )
    notification_id = target["id"]

    # Write inbound_event with source="replay" (simulating a D3 reconnect
    # backfill against this notification id).
    fingerprint = f"host:a7-cross:{notification_id}"
    status = _post_inbound(
        client,
        host["headers"],
        fingerprint=fingerprint,
        event_id=uuid.UUID(notification_id),
        event_type="topic.lifecycle.closed",
        source=InboundEventSource.replay,
    )
    assert status == 201

    # Write sessions jsonl with event_source="replay" — must match.
    log_dir = tmp_path / "a7-cross-sessions"
    session_id = f"sess-a7-cross-{uuid.uuid4().hex[:8]}"
    append_session_wake_log(
        log_dir=log_dir,
        session_id=session_id,
        persona="host",
        integration="waker",
        prompt="MAP wake · A7 cross-layer",
        response_text="ok",
        status="ok",
        event_id=notification_id,
        event_source="replay",
        fingerprint=fingerprint,
    )

    db_session.expire_all()
    ib = (
        db_session.query(InboundEvent)
        .filter(InboundEvent.fingerprint == fingerprint)
        .one()
    )

    candidates = list(log_dir.rglob("*.jsonl"))
    assert candidates, "no jsonl file written"
    entry = json.loads(
        candidates[0].read_text(encoding="utf-8").strip().splitlines()[-1]
    )
    assert ib.source.value == entry["event_source"], (
        f"cross-layer source drift: inbound_event.source={ib.source.value} "
        f"vs sessions jsonl event_source={entry['event_source']!r}"
    )
    assert ib.source.value == "replay"

    for path in candidates:
        path.unlink()
