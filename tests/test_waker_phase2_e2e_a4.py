"""Phase 2 I5-A4: 重复唤醒率 < 0.1% (dedup across SSE / replay / polling).

Validates reviewer redline: same fingerprint arriving via any combination of
the three paths (SSE long-poll, D3 reconnect replay, polling 兜底) must
result in **at most 1** actual ``backend.wake_async`` call. The metric is
the waker-side wake rate (real ``wake_async`` invocations), not the server
UNIQUE gate (which is the existing ``tests/test_waker_phase2_acceptance.py``
A4/A5 coverage).

Dedup stack (every layer contributes to "实际 resume ≤ 1"):
  - **D4 client-side rate limit** (60s window): blocks rapid SSE re-deliveries
    of the same fingerprint.
  - **TTL gate via ``_should_skip_event``** (30min default): blocks
    reconnect/replay re-wakes of an event we already woke. Added to
    ``_wake_event`` in this I5-A4 cycle; previously only the polling path
    had it.
  - **D6 server UNIQUE gate** (``inbound_event.UNIQUE(fingerprint)``): final
    hard gate that blocks cross-process duplicates regardless of client state.

Tests exercise three scenarios:
  - **SSE storm** — 1000 SSE frames with the same fingerprint arrive in rapid
    succession (within D4 60s window).
  - **SSE wake → reconnect → replay** — a single SSE wake fires; SSE then
    drops; on reconnect, ``_sse_replay_unread`` re-delivers the same
    fingerprint. Within TTL, the replay must be suppressed.
  - **Replay flood** — 50 unread notifications with the same fingerprint
    flood through ``_sse_replay_unread``.

Design:
  - Stub backend (PersonaAgentWakeBackend subclass) capturing every wake.
  - Stub MapCommandClient with controlled ``notifications_unread`` and
    ``inbound_event_record``.
  - All fingerprint IDs are derived from the same notification id so the
    waker computes the same fingerprint for every path.
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
    WakeResult,
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubBackend(PersonaAgentWakeBackend):
    """PersonaAgentWakeBackend subclass that captures every wake_async call.

    Returns instantly (no CLI subprocess); tests assert on call counts.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.reset_calls = 0

    async def connect(self) -> None:  # pragma: no cover
        return None

    async def disconnect(self) -> None:  # pragma: no cover
        return None

    async def reset_session(self) -> None:
        # _prepare_session_for_event calls this when context key changes;
        # track for assertion if needed.
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
    """MapCommandClient with controlled notifications_unread + always-first record.

    ``inbound_event_record`` always returns True on first sighting (server
    would actually return False for cross-process dup, but the stub is the
    single source of truth here — the D6 server UNIQUE gate is covered
    separately in ``test_waker_phase2_acceptance.py``).
    """

    def __init__(self, unread_notifications: list[dict[str, Any]] | None = None) -> None:
        self._unread = unread_notifications or []
        self.record_calls: list[dict[str, Any]] = []

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
        return True  # first sighting — wake should proceed (subject to TTL)


def _make_same_notification(notification_id: str | None = None) -> dict[str, Any]:
    """Build a single notification; tests reuse this id to keep fingerprint
    identical across multiple deliveries.
    """
    nid = notification_id or str(uuid.uuid4())
    return {
        "id": nid,
        "event": "agent.mentioned",
        "summary": "A4 dup probe",
        "target_id": "a4-target-fixed",
        "payload_json": {
            "kind": "mention",
            "topic_id": "00000000-0000-0000-0000-000000000a04",
            "comment_id": "a4-comment-fixed",
            "notification_id": nid,
        },
        "wake_version": 1,
    }


def _make_waker(
    *,
    unread: list[dict[str, Any]],
    tmp_path: Path,
    persona: str = "host",
) -> tuple[RuntimeWaker, _StubBackend, _StubMapClient]:
    cfg = RuntimeWakerConfig(
        persona=persona,
        project_root=tmp_path,
        state_file=tmp_path / f"state-{persona}.json",
        sse_enabled=False,
    )
    backend = _StubBackend()
    client = _StubMapClient(unread_notifications=unread)
    waker = RuntimeWaker(client=client, config=cfg, backend=backend)
    return waker, backend, client


def _expected_fingerprint(notif_id: str) -> str:
    """Compute the same fingerprint the waker will derive.

    ``_build_sse_wake_event`` produces ``{persona}:{kind}:{notification_id}:{wake_version}``.
    ``agent.mentioned`` doesn't match any kind-router prefix (see I2+I3
    log §设计要点 3) so it falls through to ``"notification"``.
    """
    return f"host:notification:{notif_id}:1"


# ---------------------------------------------------------------------------
# Test 1: SSE storm — 1000 same-fingerprint events in rapid succession
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a4_sse_storm_1000_same_fingerprint_yields_one_wake(
    tmp_path: Path,
) -> None:
    """1000 SSE frames with the same fingerprint must collapse to 1 wake.

    Mechanism stack (in order, first to short-circuit wins):
      - D4 client-side rate limit (60s window) catches 999 of 1000.
      - TTL gate would catch the rest if D4 was disabled; here D4 does the
        work but the test still validates the end-to-end invariant.

    The reviewer redline says < 0.1% duplicate wake rate; 1 wake out of 1000
    deliveries is 0.1%, on the threshold — anything > 1 is a fail.
    """
    notif_id = str(uuid.uuid4())
    notification = _make_same_notification(notif_id)
    waker, backend, client = _make_waker(
        unread=[notification], tmp_path=tmp_path
    )

    # Drive 1000 _wake_event calls with same fingerprint + SSE source.
    # In production these would come from SSE long-poll re-deliveries; here
    # we invoke _wake_event directly to keep the test deterministic and fast.
    fingerprint = _expected_fingerprint(notif_id)
    from cli.runtime_waker import WakeEvent

    event = WakeEvent(
        persona="host",
        kind="notification",
        object_id="a4-target-fixed",
        fingerprint=fingerprint,
        title="A4 SSE storm",
        reason="phase 2 A4 SSE storm probe",
        payload=dict(notification["payload_json"]),
    )

    stats = RuntimeWakerStats()
    n_deliveries = 1000
    for _ in range(n_deliveries):
        await waker._wake_event(event, event_source=WAKE_SOURCE_SSE, stats=stats)

    # Primary invariant: ≤ 1 actual wake
    assert len(backend.calls) <= 1, (
        f"SSE storm: {len(backend.calls)} wakes for {n_deliveries} deliveries "
        f"(must be ≤ 1)"
    )

    # D4 client-side rate limit catches the bulk within 60s; TTL event-cooldown
    # catches any straggler outside the 60s window. Either stat is acceptable
    # evidence of dedup — what's NOT acceptable is a second wake_async call.
    total_skips = stats.sse_rate_limit_skips + stats.wake_skips
    assert total_skips >= n_deliveries - 1, (
        f"dedup skipped {total_skips}/{n_deliveries - 1} expected; "
        f"d4_skips={stats.sse_rate_limit_skips} ttl_skips={stats.wake_skips}"
    )

    # All record calls have the same fingerprint (no fingerprint collision)
    assert len(client.record_calls) == 1, (
        f"first-sighting record should fire once, got {len(client.record_calls)}"
    )

    print(
        f"[a4-sse-storm] deliveries={n_deliveries} "
        f"wakes={len(backend.calls)} "
        f"d4_skips={stats.sse_rate_limit_skips} "
        f"ttl_skips={stats.wake_skips}"
    )


# ---------------------------------------------------------------------------
# Test 2: SSE wake + reconnect + replay delivers same fingerprint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a4_sse_wake_then_replay_same_fingerprint_within_ttl(
    tmp_path: Path,
) -> None:
    """SSE wakes the fingerprint; SSE drops; on reconnect ``_sse_replay_unread``
    re-delivers the same fingerprint. Within TTL, the replay must NOT call
    ``wake_async`` again.

    This is the key scenario surfaced by I5-A4 review: without the TTL gate
    on ``_wake_event``, replay path bypassed D4 (replay豁免) and would call
    ``wake_async`` a second time even when we just woke the same fingerprint.
    """
    notif_id = str(uuid.uuid4())
    notification = _make_same_notification(notif_id)
    expected_fp = _expected_fingerprint(notif_id)

    waker, backend, client = _make_waker(
        unread=[notification], tmp_path=tmp_path
    )

    stats = RuntimeWakerStats()

    # Step 1: SSE consumes 1 frame → 1 wake.
    # Hand-roll a minimal SSE response carrying 1 frame, then closes.
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
        # Stream ends → consumer exits.

    response.aiter_text = _aiter_text  # type: ignore[method-assign]
    stop = asyncio.Event()
    await waker._sse_consume_stream(response, stats, stop)

    assert len(backend.calls) == 1
    assert backend.calls[0]["event_source"] == WAKE_SOURCE_SSE
    assert backend.calls[0]["fingerprint"] == expected_fp

    # Step 2: simulate disconnect + reconnect — server now has the same
    # notification still unread. ``_sse_replay_unread`` reads it back.
    await waker._sse_replay_unread(stats)

    # After replay, the same fingerprint must NOT be re-woke within TTL.
    assert len(backend.calls) == 1, (
        f"SSE+replay: expected 1 total wake, got {len(backend.calls)} "
        f"(TTL gate must suppress replay of already-woken fingerprint)"
    )

    # Stats: replay run fired once. ``sse_replay_wakes_sent`` counts replay-loop
    # iterations (one per unread entry processed), NOT actual wake_async calls —
    # so 1 here means the loop iterated once; the TTL gate then suppressed the
    # actual wake. PRIMARY invariant stays ``backend.calls == 1``.
    assert stats.sse_replay_runs == 1
    assert stats.sse_replay_wakes_sent == 1, (
        f"replay loop iterated {stats.sse_replay_wakes_sent} times "
        f"(expected 1 — single unread entry)"
    )
    assert stats.wake_skips >= 1, (
        f"TTL gate must skip ≥ 1 (already-woken fingerprint), got {stats.wake_skips}"
    )

    # No new record_calls either — TTL gate short-circuits before record
    assert len(client.record_calls) == 1, (
        f"only the original SSE wake should record; "
        f"got {len(client.record_calls)} records"
    )

    print(
        f"[a4-sse-then-replay] sse_wakes=1 replay_wakes=0 "
        f"total={len(backend.calls)} ttl_skips={stats.wake_skips}"
    )


# ---------------------------------------------------------------------------
# Test 3: Replay-only flood — 50 unread entries with same fingerprint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a4_replay_flood_50_same_fingerprint_yields_one_wake(
    tmp_path: Path,
) -> None:
    """50 unread notifications with the same fingerprint flooded through
    ``_sse_replay_unread`` must collapse to 1 wake.

    Note: ``_sse_replay_unread`` skips notifications whose ``id`` is no
    longer in ``notifications_unread`` (already read), but for the same
    notification id across 50 entries the waker derives the same
    fingerprint and the TTL gate suppresses 49 of 50.

    In production, the SSE long-poll stream + reconnect replay shouldn't
    deliver the same notification 50 times in a row, but a misbehaving
    server / reconnection storm could. This test pins the dedup invariant
    regardless of how chatty the source is.
    """
    notif_id = str(uuid.uuid4())
    notification = _make_same_notification(notif_id)

    # 50 copies of the same notification id — waker will derive same fp
    unread = [notification for _ in range(50)]

    waker, backend, client = _make_waker(unread=unread, tmp_path=tmp_path)

    stats = RuntimeWakerStats()
    await waker._sse_replay_unread(stats)

    # Primary invariant: exactly 1 wake (first delivery wakes; TTL gate
    # suppresses the rest even though D4 is bypassed for replay).
    assert len(backend.calls) == 1, (
        f"replay flood: expected 1 wake for 50 same-fingerprint deliveries, "
        f"got {len(backend.calls)}"
    )

    # The single wake is tagged replay (came via _sse_replay_unread).
    assert backend.calls[0]["event_source"] == WAKE_SOURCE_REPLAY
    assert backend.calls[0]["fingerprint"] == _expected_fingerprint(notif_id)

    # TTL gate should have suppressed 49 — wake_skips counts that path.
    # Note: stats.wake_skips is also incremented by polling path's
    # _should_skip_event check, so it's a meaningful counter for both paths.
    assert stats.wake_skips >= 49, (
        f"TTL gate should skip 49+ duplicates; got {stats.wake_skips}"
    )

    # Replay run fired once; ``sse_replay_wakes_sent`` counts notifications
    # processed (one per unread entry), not actual wake_async invocations.
    # The PRIMARY invariant is ``len(backend.calls) == 1`` above; the run +
    # sent counters are informational.
    assert stats.sse_replay_runs == 1
    assert stats.sse_replay_wakes_sent == 50, (
        f"replay loop iterated 50 times (one per unread entry), "
        f"got {stats.sse_replay_wakes_sent}"
    )

    # Only 1 record_call (TTL gate short-circuits before record).
    assert len(client.record_calls) == 1

    print(
        f"[a4-replay-flood] deliveries=50 wakes=1 "
        f"ttl_skips={stats.wake_skips} replay_wakes_sent={stats.sse_replay_wakes_sent}"
    )


# ---------------------------------------------------------------------------
# Test 4: Mixed paths — SSE + replay + polling all hit same fingerprint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a4_mixed_paths_same_fingerprint_yields_one_wake(
    tmp_path: Path,
) -> None:
    """The same fingerprint arrives via three paths in order:
    (1) SSE long-poll frame
    (2) reconnect replay (D3 backfill)
    (3) polling cycle (兜底)
    Within TTL, only path (1) should fire wake_async.

    This is the end-to-end version of the metric: across all three paths,
    the duplicate rate stays < 0.1% (≤ 1 wake per fingerprint).
    """
    notif_id = str(uuid.uuid4())
    notification = _make_same_notification(notif_id)

    waker, backend, client = _make_waker(
        unread=[notification], tmp_path=tmp_path
    )

    stats = RuntimeWakerStats()

    # Path 1: SSE wake (using the consumer with a synthetic 1-frame stream)
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

    assert len(backend.calls) == 1, "SSE path should wake once"
    assert backend.calls[0]["event_source"] == WAKE_SOURCE_SSE

    # Path 2: reconnect replay — server still has it unread
    await waker._sse_replay_unread(stats)
    assert len(backend.calls) == 1, "replay path should not re-wake (TTL gate)"

    # Path 3: polling cycle. _run_once_async discovers the same fingerprint via
    # the unread notifications list; the polling path's _should_skip_event
    # filter catches it (per-event cooldown + persona_inflight). Capture the
    # returned stats so we can assert the skip was counted.
    polling_stats = await waker._run_once_async()
    polling_wakes = len(backend.calls) - 1
    assert polling_wakes == 0, (
        f"polling cycle re-woke {polling_wakes} times "
        f"(must be 0 — _should_skip_event filters in _run_once_async)"
    )

    # Final tally: exactly 1 wake across all three paths.
    assert len(backend.calls) == 1, (
        f"mixed paths: expected 1 total wake, got {len(backend.calls)}"
    )

    # Replay path's TTL skip counted in `stats.wake_skips`.
    # Polling path's _should_skip_event counted in `polling_stats.wake_skips`.
    assert stats.wake_skips >= 1, (
        f"replay TTL should skip ≥ 1, got {stats.wake_skips}"
    )
    assert polling_stats.wake_skips >= 1, (
        f"polling _should_skip_event should skip ≥ 1, "
        f"got {polling_stats.wake_skips}"
    )

    print(
        f"[a4-mixed-paths] total_wakes=1 "
        f"replay_ttl_skips={stats.wake_skips} "
        f"polling_skips={polling_stats.wake_skips} "
        f"record_calls={len(client.record_calls)}"
    )


# ---------------------------------------------------------------------------
# Test 5: Different fingerprints via replay — wake each (regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a4_distinct_fingerprints_via_replay_all_wake(
    tmp_path: Path,
) -> None:
    """Regression guard: TTL gate must NOT suppress distinct fingerprints.

    Replay path delivers 5 notifications with different ids → 5 distinct
    fingerprints → 5 wakes. If TTL gate wrongly suppresses distinct
    fingerprints, this fails (and breaks A2 漏事件率 = 0).
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

    print(
        f"[a4-distinct-fps] wakes=5 all_replay "
        f"distinct_fingerprints={len(set(fingerprints))}"
    )


# ---------------------------------------------------------------------------
# Test 6: baseline JSON emitter for I6 consolidation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a4_baseline_emitted(tmp_path: Path) -> None:
    """Write A4 baseline (per-path wake rate) to JSON for I6 consolidation.

    Canonical path ``.map/generated-plans/phase2-p95-baseline.json`` is
    written by the host session at the end of I5; this test emits a
    ``tmp_path`` snapshot for offline review.
    """
    import json

    summary = {
        "produced_at": "phase2-i5-a4",
        "metric": "重复唤醒率 (同一 fingerprint 经 SSE+replay+polling 三路径后的实际 backend.wake_async 调用次数)",
        "redline": "< 0.1% (≤ 1 wake per fingerprint)",
        "scenarios": [
            {
                "name": "sse_storm",
                "deliveries": 1000,
                "expected_wakes": 1,
                "gate": "D4 client-side rate limit (60s window)",
            },
            {
                "name": "sse_then_replay_within_ttl",
                "deliveries": 2,
                "expected_wakes": 1,
                "gate": "TTL via _should_skip_event (30min default)",
            },
            {
                "name": "replay_flood_same_fingerprint",
                "deliveries": 50,
                "expected_wakes": 1,
                "gate": "TTL via _should_skip_event (replay bypasses D4)",
            },
            {
                "name": "mixed_paths_same_fingerprint",
                "deliveries": 3,
                "expected_wakes": 1,
                "gate": "TTL + D4 + polling _should_skip_event",
            },
            {
                "name": "distinct_fingerprints_via_replay",
                "deliveries": 5,
                "expected_wakes": 5,
                "gate": "regression guard — TTL must NOT suppress distinct fps",
            },
        ],
        "implementation_note": (
            "I5-A4 added _should_skip_event check at top of _wake_event "
            "so SSE/replay paths get the same TTL/persona-infight guard "
            "the polling path already had."
        ),
    }
    out = tmp_path / "phase2-a4-baseline.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    assert out.exists()
    print(f"[a4] baseline emitted to {out}")
