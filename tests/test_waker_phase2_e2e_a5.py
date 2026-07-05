"""Phase 2 I5-A5: 幂等写成功率 100%（server-side UNIQUE gate 不受 D4 补漏豁免影响）。

Validates reviewer redline: the server-side ``inbound_event.UNIQUE(fingerprint)``
gate is the authoritative dedup, and it stays authoritative even when client-side
gates are bypassed (D4 replay bypass) or when only one waker process is active.

Server-side coverage (TestClient hitting the real API endpoint):
- 100 same-fingerprint POSTs cycling through polling/sse/replay → 1 + 99.
- 50 same-fingerprint POSTs with source=replay → 1 + 49.

Already covered in ``tests/test_waker_phase2_acceptance.py:test_a5_replay_rejection_holds_across_sources``
and ``test_waker_phase2_acceptance.py:test_a4_replay_replay_path_only_one_row``.

This file focuses on the **end-to-end client-side pipeline** showing how the waker
responds to server 409 rejections and how client-side gates collaborate with
server-side UNIQUE:

  - Test 1: 100 SSE-storm same-fingerprint (no --force) → 1 wake_async + 99 dedup-skipped
    (D4 60s window does most of the work).
  - Test 2: 50 replay same-fingerprint (no --force) → 1 wake_async + 49 wake_skips
    (TTL gate covers the gap that D4 bypass leaves open).
  - Test 3: Server UNIQUE returns False from the very first call → 0 wake_async
    (server rejection blocks the first attempt when no prior local claim exists).
  - Test 4: Server UNIQUE returns True once, then False → 1 wake_async; subsequent
    same-fingerprint calls within the same fingerprint's history stay suppressed
    by TTL (the heartbeat re-eval path is documented but not exercised here).
  - Test 5: Cross-source mix (polling + sse + replay) on the same fingerprint →
    exactly 1 wake_async across all paths.
  - Test 6: Distinct fingerprints via replay all wake (regression guard for A2).
  - Test 7: Baseline JSON emitter for I6 consolidation.

Mechanism stack reminder:
  - TTL event-cooldown (30min default) — applied at top of ``_wake_event``.
  - D4 client-side rate limit (60s) — bypassed for ``event_source="replay"``.
  - Server UNIQUE gate (``inbound_event_record`` returning False) — last defense.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

from cli.runtime_waker import (
    MapCommandClient,
    PersonaAgentWakeBackend,
    RuntimeWaker,
    RuntimeWakerConfig,
    RuntimeWakerStats,
    WAKE_SOURCE_REPLAY,
    WAKE_SOURCE_SSE,
    WakeEvent,
    WakeResult,
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubBackend(PersonaAgentWakeBackend):
    """PersonaAgentWakeBackend subclass capturing every wake_async call.

    Returns instantly; tests assert on call counts.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.reset_calls = 0

    async def connect(self) -> None:  # pragma: no cover
        return None

    async def disconnect(self) -> None:  # pragma: no cover
        return None

    async def reset_session(self) -> None:
        self.reset_calls += 1

    async def wake_async(
        self,
        *,
        prompt: str,
        event_id: str | None = None,
        event_source: str = "polling",
        fingerprint: str | None = None,
    ) -> WakeResult:
        self.calls.append(
            {
                "event_id": event_id,
                "event_source": event_source,
                "fingerprint": fingerprint,
            }
        )
        return WakeResult(session_id=f"stub-{event_id or 'no-id'}")


class _StubMapClient(MapCommandClient):
    """MapCommandClient with controlled server-side UNIQUE behavior.

    ``server_first_sighting`` controls whether ``inbound_event_record`` returns
    True (server says "you're the first") or False (server says "someone else
    claimed this fingerprint"). The stub also tracks every record_call so
    tests can assert on how many round-trips the waker made.

    For Test 3 (server returns False from the start), set
    ``server_first_sighting=False`` and verify 0 wake_async calls.

    For Test 4 (server returns True once then False), set
    ``server_first_sighting=True`` for the first call and verify subsequent
    same-fingerprint calls are caught by TTL gate (no record calls).

    This is a stand-in for the real server UNIQUE gate covered in
    ``tests/test_waker_phase2_acceptance.py``.
    """

    def __init__(
        self,
        *,
        unread_notifications: list[dict[str, Any]] | None = None,
        server_first_sighting: bool = True,
    ) -> None:
        self._unread = unread_notifications or []
        self._server_first_sighting = server_first_sighting
        self.record_calls: list[dict[str, Any]] = []
        self._seen_fingerprints: set[str] = set()

    def whoami(self) -> dict[str, Any]:
        return {"id": "stub-agent-uuid", "name": "host"}

    def todos(self) -> dict[str, Any]:
        return {}

    def notifications_unread(
        self,
        *,
        limit: int = 50,
        category: str | None = "wakeable",
    ) -> list[dict[str, Any]]:
        return list(self._unread)

    def inbound_event_record(
        self,
        *,
        event_id: str,
        fingerprint: str,
        event_type: str,
        source: str = "polling",
    ) -> bool:
        self.record_calls.append(
            {
                "event_id": event_id,
                "fingerprint": fingerprint,
                "event_type": event_type,
                "source": source,
            }
        )
        if not self._server_first_sighting:
            return False
        # Mirror real server UNIQUE: first sighting wins.
        if fingerprint in self._seen_fingerprints:
            return False
        self._seen_fingerprints.add(fingerprint)
        return True


def _make_same_notification(notification_id: str | None = None) -> dict[str, Any]:
    nid = notification_id or str(uuid.uuid4())
    return {
        "id": nid,
        "event": "agent.mentioned",
        "summary": "A5 idempotency probe",
        "target_id": "a5-target-fixed",
        "payload_json": {
            "kind": "mention",
            "topic_id": "00000000-0000-0000-0000-000000000a05",
            "comment_id": "a5-comment-fixed",
            "notification_id": nid,
        },
        "wake_version": 1,
    }


def _make_waker(
    *,
    unread: list[dict[str, Any]],
    tmp_path: Path,
    persona: str = "host",
    server_first_sighting: bool = True,
    force: bool = False,
) -> tuple[RuntimeWaker, _StubBackend, _StubMapClient]:
    cfg = RuntimeWakerConfig(
        persona=persona,
        project_root=tmp_path,
        state_file=tmp_path / f"state-{persona}.json",
        sse_enabled=False,
        force=force,
    )
    backend = _StubBackend()
    client = _StubMapClient(
        unread_notifications=unread,
        server_first_sighting=server_first_sighting,
    )
    waker = RuntimeWaker(client=client, config=cfg, backend=backend)
    return waker, backend, client


def _expected_fingerprint(notif_id: str) -> str:
    """``agent.mentioned`` doesn't match any kind-router prefix (see I2+I3
    log §设计要点 3) so it falls through to ``"notification"``.
    """
    return f"host:notification:{notif_id}:1"


def _make_wake_event(notif_id: str) -> WakeEvent:
    fingerprint = _expected_fingerprint(notif_id)
    notif = _make_same_notification(notif_id)
    return WakeEvent(
        persona="host",
        kind="notification",
        object_id="a5-target-fixed",
        fingerprint=fingerprint,
        title="A5 idempotency probe",
        reason="phase 2 A5 idempotency probe",
        payload=dict(notif["payload_json"]),
    )


# ---------------------------------------------------------------------------
# Test 1: SSE-storm 100 same-fingerprint → 1 wake_async (D4 + TTL cooperate)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a5_sse_storm_100_same_fingerprint_yields_one_wake(
    tmp_path: Path,
) -> None:
    """100 SSE frames with the same fingerprint in rapid succession must
    collapse to 1 ``backend.wake_async`` call.

    Mechanism stack (first to short-circuit wins):
      - D4 client-side rate limit (60s) catches 99 of 100.
      - TTL gate would catch any straggler outside the 60s window.

    Primary invariant: ≤ 1 wake for 100 deliveries.
    """
    notif_id = str(uuid.uuid4())
    notification = _make_same_notification(notif_id)
    waker, backend, client = _make_waker(
        unread=[notification], tmp_path=tmp_path
    )

    event = _make_wake_event(notif_id)

    stats = RuntimeWakerStats()
    n_deliveries = 100
    for _ in range(n_deliveries):
        await waker._wake_event(event, event_source=WAKE_SOURCE_SSE, stats=stats)

    assert len(backend.calls) <= 1, (
        f"SSE storm: {len(backend.calls)} wakes for {n_deliveries} deliveries "
        f"(must be ≤ 1)"
    )

    total_skips = stats.sse_rate_limit_skips + stats.wake_skips
    assert total_skips >= n_deliveries - 1, (
        f"dedup skipped {total_skips}/{n_deliveries - 1} expected; "
        f"d4_skips={stats.sse_rate_limit_skips} ttl_skips={stats.wake_skips}"
    )

    # Server-side record_calls: ≤ 1 (first sighting wins; rest are caught client-side).
    assert len(client.record_calls) <= 1, (
        f"server record_calls: {len(client.record_calls)} "
        f"(should be ≤ 1; client gates should prevent duplicate server round-trips)"
    )

    print(
        f"[a5-sse-storm] deliveries={n_deliveries} "
        f"wakes={len(backend.calls)} "
        f"d4_skips={stats.sse_rate_limit_skips} "
        f"ttl_skips={stats.wake_skips} "
        f"server_records={len(client.record_calls)}"
    )


# ---------------------------------------------------------------------------
# Test 2: Replay flood 50 same-fingerprint → 1 wake_async (TTL alone)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a5_replay_50_same_fingerprint_yields_one_wake(
    tmp_path: Path,
) -> None:
    """50 unread notifications with the same fingerprint flooded through
    ``_sse_replay_unread`` must collapse to 1 wake.

    D4 is bypassed for replay (plan §D4 补漏豁免); TTL gate must do all the work.
    """
    notif_id = str(uuid.uuid4())
    notification = _make_same_notification(notif_id)
    unread = [notification for _ in range(50)]

    waker, backend, client = _make_waker(unread=unread, tmp_path=tmp_path)

    stats = RuntimeWakerStats()
    await waker._sse_replay_unread(stats)

    assert len(backend.calls) == 1, (
        f"replay flood: expected 1 wake for 50 same-fingerprint deliveries, "
        f"got {len(backend.calls)}"
    )
    assert backend.calls[0]["event_source"] == WAKE_SOURCE_REPLAY
    assert backend.calls[0]["fingerprint"] == _expected_fingerprint(notif_id)

    # TTL gate should have suppressed 49 wake attempts.
    assert stats.wake_skips >= 49, (
        f"TTL gate should skip 49+ duplicates; got {stats.wake_skips}"
    )

    # Server-side: only 1 record (TTL gate short-circuits before server call).
    assert len(client.record_calls) == 1, (
        f"only 1 server record expected (TTL catches the rest); "
        f"got {len(client.record_calls)}"
    )

    print(
        f"[a5-replay-flood] deliveries=50 wakes=1 "
        f"ttl_skips={stats.wake_skips} server_records={len(client.record_calls)}"
    )


# ---------------------------------------------------------------------------
# Test 3: Server UNIQUE returns False from the start → 0 wake_async
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a5_server_first_call_rejects_no_wake(
    tmp_path: Path,
) -> None:
    """When the server-side UNIQUE gate rejects the very first call (no
    prior local claim), the waker must NOT call ``backend.wake_async``.

    This is the core A5 invariant: **server UNIQUE is the authoritative
    gate, even on the first attempt**. Without it, a duplicate waker
    process could race the original and both wake the agent.

    Stub: ``server_first_sighting=False`` — every record_call returns False.
    """
    notif_id = str(uuid.uuid4())
    notification = _make_same_notification(notif_id)
    waker, backend, client = _make_waker(
        unread=[notification],
        tmp_path=tmp_path,
        server_first_sighting=False,
    )

    event = _make_wake_event(notif_id)
    stats = RuntimeWakerStats()

    # Single attempt: server UNIQUE rejects → 0 wake_async.
    await waker._wake_event(event, event_source=WAKE_SOURCE_SSE, stats=stats)

    assert len(backend.calls) == 0, (
        f"server UNIQUE 409 on first call: expected 0 wake_async, "
        f"got {len(backend.calls)}"
    )

    # The waker should have called record exactly once (rejected by server).
    assert len(client.record_calls) == 1, (
        f"server record should be attempted once, got {len(client.record_calls)}"
    )

    print(
        "[a5-server-first-rejects] wakes=0 server_records=1 "
        "(server UNIQUE blocked first attempt)"
    )


@pytest.mark.asyncio
async def test_a5_server_first_call_rejects_replay_path_no_wake(
    tmp_path: Path,
) -> None:
    """Same as Test 3 but via the replay path (event_source="replay").

    Confirms the replay-bypass of D4 does NOT bypass server UNIQUE — the
    reviewer redline from plan §A5.

    Stub: server returns False. Replay path sends 5 same-fingerprint
    notifications; the first one is processed by TTL (no prior claim) and
    immediately rejected by server; subsequent 4 are caught by TTL gate.
    """
    notif_id = str(uuid.uuid4())
    notification = _make_same_notification(notif_id)
    unread = [notification for _ in range(5)]

    waker, backend, client = _make_waker(
        unread=unread,
        tmp_path=tmp_path,
        server_first_sighting=False,
    )

    stats = RuntimeWakerStats()
    await waker._sse_replay_unread(stats)

    assert len(backend.calls) == 0, (
        f"server UNIQUE 409 on replay path: expected 0 wake_async, "
        f"got {len(backend.calls)}"
    )

    # Replay loop iterated 5 times but only the first reached the server
    # (TTL caught the rest before server round-trip).
    assert stats.sse_replay_wakes_sent == 5
    assert len(client.record_calls) == 1, (
        f"server record should be attempted once (TTL caught subsequent), "
        f"got {len(client.record_calls)}"
    )

    print(
        "[a5-server-replay-rejects] wakes=0 server_records=1 "
        "replay_iterations=5"
    )


# ---------------------------------------------------------------------------
# Test 4: Server UNIQUE accepts first, then client gates prevent subsequent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a5_server_first_call_accepts_then_client_gates_protect(
    tmp_path: Path,
) -> None:
    """Server accepts the first call → 1 wake_async. Subsequent calls for
    the same fingerprint within the TTL window are blocked by the client-
    side TTL gate (so the server sees exactly 1 record, not 100).
    """
    notif_id = str(uuid.uuid4())
    notification = _make_same_notification(notif_id)
    waker, backend, client = _make_waker(
        unread=[notification],
        tmp_path=tmp_path,
        server_first_sighting=True,
    )

    event = _make_wake_event(notif_id)
    stats = RuntimeWakerStats()

    # 100 deliveries via SSE path.
    for _ in range(100):
        await waker._wake_event(event, event_source=WAKE_SOURCE_SSE, stats=stats)

    assert len(backend.calls) == 1, (
        f"100 SSE deliveries: expected 1 wake (server accepts first, "
        f"client gates suppress rest), got {len(backend.calls)}"
    )

    # Server saw exactly 1 record (client TTL/D4 prevented duplicates).
    assert len(client.record_calls) == 1, (
        f"server record should be 1 (TTL/D4 stop duplicates client-side), "
        f"got {len(client.record_calls)}"
    )

    print(
        f"[a5-first-accept] wakes=1 server_records=1 "
        f"client_skipped={stats.sse_rate_limit_skips + stats.wake_skips}"
    )


# ---------------------------------------------------------------------------
# Test 5: Cross-source mix (polling + sse + replay) → exactly 1 wake
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a5_cross_source_mix_same_fingerprint_yields_one_wake(
    tmp_path: Path,
) -> None:
    """Three paths hit the same fingerprint in sequence: SSE → replay → polling.
    Primary invariant: exactly 1 ``backend.wake_async`` total.

    This is the **integration-level A5 check** — every path's gate must
    cooperate so the agent is woken exactly once.
    """
    notif_id = str(uuid.uuid4())
    notification = _make_same_notification(notif_id)
    expected_fp = _expected_fingerprint(notif_id)

    waker, backend, client = _make_waker(
        unread=[notification], tmp_path=tmp_path
    )

    stats = RuntimeWakerStats()

    # Path 1: SSE consumes 1 frame → 1 wake (TTL allows first sighting).
    import httpx

    frame_chunk = (
        f'data: {{"type":"notification.created",'
        f'"event":"agent.mentioned",'
        f'"notification_id":"{notif_id}"}}\n\n'
    )

    request = httpx.Request("GET", "http://example/stream")
    response = httpx.Response(200, request=request)

    async def _aiter_text():
        yield frame_chunk

    response.aiter_text = _aiter_text  # type: ignore[method-assign]
    stop = asyncio.Event()
    await waker._sse_consume_stream(response, stats, stop)

    assert len(backend.calls) == 1
    assert backend.calls[0]["event_source"] == WAKE_SOURCE_SSE
    assert backend.calls[0]["fingerprint"] == expected_fp

    # Path 2: reconnect replay — same notification still unread.
    await waker._sse_replay_unread(stats)
    assert len(backend.calls) == 1, "replay path must NOT re-wake (TTL gate)"

    # Path 3: polling cycle. _run_once_async discovers the same fingerprint;
    # the polling path's _should_skip_event filter catches it.
    polling_stats = await waker._run_once_async()
    assert len(backend.calls) == 1, "polling path must NOT re-wake"

    # Exactly 1 server record across all three paths.
    assert len(client.record_calls) == 1, (
        f"server should see 1 record total (TTL/D4 stop duplicates), "
        f"got {len(client.record_calls)}"
    )

    # Stats sanity.
    assert stats.wake_skips >= 1, "replay TTL skip must be counted"
    assert polling_stats.wake_skips >= 1, "polling skip must be counted"

    print(
        f"[a5-cross-source] wakes=1 server_records=1 "
        f"replay_ttl_skips={stats.wake_skips} "
        f"polling_skips={polling_stats.wake_skips}"
    )


# ---------------------------------------------------------------------------
# Test 6: Distinct fingerprints via replay all wake (A2 regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a5_distinct_fingerprints_via_replay_all_wake(
    tmp_path: Path,
) -> None:
    """Regression guard: TTL gate must NOT suppress distinct fingerprints.

    5 distinct fingerprints via replay → 5 wakes. If TTL gate wrongly
    suppresses distinct fingerprints, this fails (and breaks A2 漏事件率 = 0).
    """
    notifs = [_make_same_notification(str(uuid.uuid4())) for _ in range(5)]
    waker, backend, client = _make_waker(unread=notifs, tmp_path=tmp_path)

    stats = RuntimeWakerStats()
    await waker._sse_replay_unread(stats)

    assert len(backend.calls) == 5, (
        f"distinct fingerprints: expected 5 wakes, got {len(backend.calls)} "
        f"(TTL gate must NOT suppress distinct fingerprints)"
    )

    fingerprints = [c["fingerprint"] for c in backend.calls]
    assert len(set(fingerprints)) == 5, "fingerprints must all be unique"

    for c in backend.calls:
        assert c["event_source"] == WAKE_SOURCE_REPLAY

    # Each distinct fingerprint triggers exactly 1 server record (TTL allows all).
    assert len(client.record_calls) == 5, (
        f"distinct fingerprints: expected 5 server records, "
        f"got {len(client.record_calls)}"
    )

    print(
        f"[a5-distinct-fps] wakes=5 all_replay "
        f"distinct_fingerprints={len(set(fingerprints))} "
        f"server_records={len(client.record_calls)}"
    )


# ---------------------------------------------------------------------------
# Test 7: Baseline JSON emitter for I6 consolidation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a5_baseline_emitted(tmp_path: Path) -> None:
    """Write A5 baseline (idempotency rate, server UNIQUE behavior) to JSON
    for I6 consolidation.

    Canonical path ``.map/generated-plans/phase2-p95-baseline.json`` is
    written by the host session at the end of I5; this test emits a
    ``tmp_path`` snapshot for offline review.
    """
    import json

    summary = {
        "produced_at": "phase2-i5-a5",
        "metric": (
            "幂等写成功率 (client-side TTL/D4 + server-side UNIQUE 集成)"
            " — 同一 fingerprint 多路径多频投递后 wake_async 调用数"
        ),
        "redline": "≤ 1 wake per fingerprint (100% idempotency)",
        "server_side_redline": (
            "服务端层重放 100 次同 fingerprint → 1 success + 99 conflicts (409); "
            "补漏路径 50 次同 fingerprint → 1 success + 49 conflicts. "
            "covered by tests/test_waker_phase2_acceptance.py:test_a5_replay_rejection_holds_across_sources "
            "+ test_a4_replay_replay_path_only_one_row."
        ),
        "scenarios": [
            {
                "name": "sse_storm_100",
                "deliveries": 100,
                "expected_wakes": 1,
                "gate": "D4 (60s) → TTL (30min)",
            },
            {
                "name": "replay_flood_50",
                "deliveries": 50,
                "expected_wakes": 1,
                "gate": "TTL (30min) — replay bypasses D4 by design",
            },
            {
                "name": "server_first_call_rejects",
                "deliveries": 1,
                "expected_wakes": 0,
                "gate": "server UNIQUE (409) — first attempt blocked",
            },
            {
                "name": "server_replay_rejects",
                "deliveries": 5,
                "expected_wakes": 0,
                "gate": "TTL catches 4 + server UNIQUE catches the first",
            },
            {
                "name": "server_accepts_then_client_protects",
                "deliveries": 100,
                "expected_wakes": 1,
                "gate": "server accepts first → client gates stop the rest",
            },
            {
                "name": "cross_source_sse_replay_polling",
                "deliveries": 3,
                "expected_wakes": 1,
                "gate": "TTL (SSE → replay) + polling _should_skip_event",
            },
            {
                "name": "distinct_fingerprints_via_replay",
                "deliveries": 5,
                "expected_wakes": 5,
                "gate": "regression guard — TTL must NOT suppress distinct fps",
            },
        ],
        "implementation_note": (
            "Plan §A5 怎么测 = 服务端层断言 100 + 50, 已在 acceptance 测试覆盖。"
            "本文件覆盖 client-side pipeline 如何处理 server 409 + client gates 协作。"
        ),
    }
    out = tmp_path / "phase2-a5-baseline.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    assert out.exists()
    print(f"[a5] baseline emitted to {out}")
