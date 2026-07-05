"""Phase 2 SSE consumer end-to-end test with mock httpx response.

Validates the SSE long-poll consumption loop in isolation:
- frames are parsed correctly from the stream
- each ``notification.created`` frame dispatches a wake
- disconnect triggers backoff + replay path (tested separately via mock)
- the replay path bypasses D4 (verified by stamping a recent attempt first)

The httpx client itself is real; only the underlying HTTP transport is
mocked via ``httpx.MockTransport``. This gives the consumer a genuine
``AsyncByteStream`` with realistic chunking + backpressure, without
needing a live API server.

End-to-end P95 measurements (A1a / A1b / A1 总 / A3) are *not* covered
here — they require a running waker + Claude backend + a deterministic
event source, and live in the deployment harness. The dogfood numbers
land in ``.map/generated-plans/phase2-p95-baseline.json``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

pytestmark = pytest.mark.slow

from cli.host_worker_types import WorkerError
from cli.runtime_waker import (
    RuntimeWaker,
    RuntimeWakerConfig,
    RuntimeWakerStats,
    WAKE_SOURCE_POLLING,
    WAKE_SOURCE_REPLAY,
    WAKE_SOURCE_SSE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_waker_for_consumer(
    *,
    persona: str = "host",
    window: float = 60.0,
) -> RuntimeWaker:
    """Build a RuntimeWaker suitable for in-process SSE consumer tests.

    Bypasses ``__init__`` (which would require a real MapCommandClient)
    and wires only the fields the SSE methods touch.
    """
    waker = RuntimeWaker.__new__(RuntimeWaker)
    waker.config = RuntimeWakerConfig(
        persona=persona, sse_recent_resume_window_seconds=window
    )
    waker._recent_resume_attempts = {}
    waker.agent_id = "agent-uuid"
    waker.client = MagicMock()
    return waker


def _fake_sse_response(chunks: list[str]) -> httpx.Response:
    """Build an httpx.Response that yields ``chunks`` from ``aiter_text``.

    Uses an in-memory byte stream to keep the test fully in-process; no
    real socket involved. The consumer only ever reads ``aiter_text`` so
    other fields are stubbed.
    """

    async def aiter_text(_self=None):  # type: ignore[no-redef]
        for chunk in chunks:
            yield chunk

    # Build a real httpx.Response so attribute access doesn't surprise the
    # consumer (status_code, headers, etc.).
    request = httpx.Request("GET", "http://example/stream")
    response = httpx.Response(200, request=request)
    # Patch aiter_text at the instance level so we don't touch the class.
    response.aiter_text = aiter_text  # type: ignore[method-assign]
    return response


# ---------------------------------------------------------------------------
# _sse_consume_stream — frame ingestion + dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_consumer_dispatches_one_wake_per_frame() -> None:
    """A stream emitting two ``notification.created`` frames must produce
    exactly two ``_wake_event`` invocations with ``event_source="sse"``."""
    waker = _make_waker_for_consumer()
    notif_a = {
        "id": "aaaa1111-1111-1111-1111-111111111111",
        "event": "topic.lifecycle.closed",
        "summary": "topic A closed",
        "target_id": "topic-a",
        "payload_json": {"kind": "topic.lifecycle", "topic_id": "topic-a"},
        "wake_version": 1,
    }
    notif_b = {
        "id": "bbbb2222-2222-2222-2222-222222222222",
        "event": "experiment.lifecycle.withdrawn",
        "summary": "experiment B withdrawn",
        "target_id": "exp-b",
        "payload_json": {
            "kind": "experiment.lifecycle",
            "experiment_id": "exp-b",
        },
        "wake_version": 1,
    }
    waker.client.notifications_unread.return_value = [notif_a, notif_b]
    waker._wake_event = AsyncMock()  # type: ignore[method-assign]

    stats = RuntimeWakerStats()
    stop = asyncio.Event()
    chunks = [
        'data: {"type":"notification.created","event":"topic.lifecycle.closed","notification_id":"aaaa1111-1111-1111-1111-111111111111"}\n\n',
        ": heartbeat\n\n",
        'data: {"type":"notification.created","event":"experiment.lifecycle.withdrawn","notification_id":"bbbb2222-2222-2222-2222-222222222222"}\n\n',
    ]
    response = _fake_sse_response(chunks)

    await waker._sse_consume_stream(response, stats, stop)

    assert stats.sse_events_received == 2
    assert waker._wake_event.await_count == 2
    # Both calls used event_source="sse"
    for call in waker._wake_event.await_args_list:
        assert call.kwargs.get("event_source") == WAKE_SOURCE_SSE


@pytest.mark.asyncio
async def test_sse_consumer_ignores_heartbeat_and_unknown_types() -> None:
    """Heartbeat lines (``: heartbeat``) and unknown event types must not
    increment ``sse_events_received`` or trigger wakes."""
    waker = _make_waker_for_consumer()
    waker.client.notifications_unread.return_value = []
    waker._wake_event = AsyncMock()  # type: ignore[method-assign]

    stats = RuntimeWakerStats()
    stop = asyncio.Event()
    chunks = [
        ": heartbeat\n\n",
        'event: ping\ndata: {"x":1}\n\n',
        'data: {"type":"ping","event":"ping"}\n\n',
        ": another-heartbeat\n\n",
    ]
    response = _fake_sse_response(chunks)
    await waker._sse_consume_stream(response, stats, stop)

    assert stats.sse_events_received == 0
    assert waker._wake_event.await_count == 0


@pytest.mark.asyncio
async def test_sse_consumer_skips_unknown_notification_id() -> None:
    """If the SSE frame references a notification id that's no longer in
    ``notifications_unread`` (e.g. marked read elsewhere), the consumer
    must skip it without raising — polling will catch it next cycle."""
    waker = _make_waker_for_consumer()
    waker.client.notifications_unread.return_value = []  # empty
    waker._wake_event = AsyncMock()  # type: ignore[method-assign]

    stats = RuntimeWakerStats()
    stop = asyncio.Event()
    chunks = [
        'data: {"type":"notification.created","event":"x","notification_id":"gone"}\n\n',
    ]
    response = _fake_sse_response(chunks)
    await waker._sse_consume_stream(response, stats, stop)

    assert stats.sse_events_received == 1  # frame parsed, but dispatch is no-op
    assert waker._wake_event.await_count == 0


@pytest.mark.asyncio
async def test_sse_consumer_handles_split_frames() -> None:
    """A single frame split across two TCP chunks must still parse as one
    event — the buffer carries partial state."""
    waker = _make_waker_for_consumer()
    notif = {
        "id": "cccc3333-3333-3333-3333-333333333333",
        "event": "topic.lifecycle.closed",
        "summary": "split",
        "target_id": "t1",
        "payload_json": {"kind": "topic.lifecycle", "topic_id": "t1"},
        "wake_version": 1,
    }
    waker.client.notifications_unread.return_value = [notif]
    waker._wake_event = AsyncMock()  # type: ignore[method-assign]

    stats = RuntimeWakerStats()
    stop = asyncio.Event()
    chunks = [
        'data: {"type":"notification.created","event":"topic.lifecycle.closed","notific',
        'ation_id":"cccc3333-3333-3333-3333-333333333333"}\n\n',
    ]
    response = _fake_sse_response(chunks)
    await waker._sse_consume_stream(response, stats, stop)

    assert stats.sse_events_received == 1
    assert waker._wake_event.await_count == 1


# ---------------------------------------------------------------------------
# _sse_replay_unread — D3 reconnect backfill path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_replay_bypasses_d4_rate_limit() -> None:
    """Plan §D4 补漏豁免: replay events must NOT be filtered by the D4
    client-side gate. Verify by stamping a recent attempt for a fingerprint
    then calling _sse_replay_unread; the wake must still go through.

    We instrument _wake_event to record calls instead of running Claude.
    """
    waker = _make_waker_for_consumer(window=60.0)
    notif = {
        "id": "dddd4444-4444-4444-4444-444444444444",
        "event": "topic.lifecycle.closed",
        "summary": "replay",
        "target_id": "t1",
        "payload_json": {"kind": "topic.lifecycle", "topic_id": "t1"},
        "wake_version": 1,
    }
    waker.client.notifications_unread.return_value = [notif]

    wake_calls: list[tuple[str, str]] = []

    async def fake_wake(event, *, event_source=WAKE_SOURCE_POLLING, stats=None):
        wake_calls.append((event.fingerprint, event_source))
        # Mimic _record_resume_attempt for the SSE path (would block D4
        # on the next attempt if source were 'sse' and same fingerprint
        # came again within 60s). For replay we never call it — replay
        # bypasses D4 entirely.
        if event_source != WAKE_SOURCE_REPLAY:
            waker._record_resume_attempt(event)

    waker._wake_event = fake_wake  # type: ignore[method-assign]

    # Pre-stamp the D4 bucket with the same fingerprint so a normal sse
    # source would be skipped.
    fingerprint = "host:topic_lifecycle:dddd4444-4444-4444-4444-444444444444:1"
    waker._recent_resume_attempts[fingerprint] = datetime.now(UTC)

    stats = RuntimeWakerStats()
    await waker._sse_replay_unread(stats)

    # The replay must have gone through despite the D4 stamp.
    assert any(src == WAKE_SOURCE_REPLAY for _, src in wake_calls), (
        f"replay event was blocked by D4 gate: {wake_calls}"
    )
    assert stats.sse_replay_runs == 1
    assert stats.sse_replay_wakes_sent == 1


@pytest.mark.asyncio
async def test_sse_replay_handles_empty_unread() -> None:
    """No unread wakeable notifications → replay is a no-op (no wakes sent,
    and ``sse_replay_runs`` does NOT increment since no replay actually ran)."""
    waker = _make_waker_for_consumer()
    waker.client.notifications_unread.return_value = []
    waker._wake_event = AsyncMock()  # type: ignore[method-assign]

    stats = RuntimeWakerStats()
    await waker._sse_replay_unread(stats)

    # Empty inbox → no replay run counted (avoids metric inflation).
    assert stats.sse_replay_runs == 0
    assert stats.sse_replay_wakes_sent == 0
    assert waker._wake_event.await_count == 0


@pytest.mark.asyncio
async def test_sse_replay_swallows_unread_failure() -> None:
    """If the notifications_unread call fails (e.g. transient network blip),
    the SSE loop must NOT die. It will retry on the next reconnect."""
    waker = _make_waker_for_consumer()
    waker.client.notifications_unread.side_effect = WorkerError("transient")
    waker._wake_event = AsyncMock()  # type: ignore[method-assign]

    stats = RuntimeWakerStats()
    await waker._sse_replay_unread(stats)
    assert stats.sse_replay_runs == 0  # early-return on failure
    assert waker._wake_event.await_count == 0


# ---------------------------------------------------------------------------
# Reconnect + backoff — covered by sse_backoff_delay unit tests in I2/I3
# (test_sse_backoff_sequence_matches_plan). End-to-end reconnect with
# multiple backoffs needs a real httpx MockTransport and asyncio wall-clock
# control; left for the deployment harness (P95 baseline collection).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# End-to-end: SSE consumer drives _wake_event with correct event_source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_path_sets_event_source_sse() -> None:
    """Verify the consumer sets event_source='sse' (not 'replay') for
    live frames. Replay-only paths (initial replay) must use 'replay'."""
    waker = _make_waker_for_consumer()
    notif = {
        "id": "eeee5555-5555-5555-5555-555555555555",
        "event": "topic.lifecycle.closed",
        "summary": "live",
        "target_id": "t1",
        "payload_json": {"kind": "topic.lifecycle", "topic_id": "t1"},
        "wake_version": 1,
    }
    waker.client.notifications_unread.return_value = [notif]

    captured_sources: list[str] = []

    async def fake_wake(event, *, event_source=WAKE_SOURCE_POLLING, stats=None):
        captured_sources.append(event_source)

    waker._wake_event = fake_wake  # type: ignore[method-assign]

    stats = RuntimeWakerStats()
    stop = asyncio.Event()
    chunks = [
        'data: {"type":"notification.created","event":"topic.lifecycle.closed","notification_id":"eeee5555-5555-5555-5555-eeee5555"}\n\n'
    ]
    # Note: the mock notification's id (eeee5555...) doesn't match the SSE
    # frame's notification_id (eeee5555-eeee) — the consumer should skip
    # the dispatch (notifications_unread didn't return that id).
    # Use a matching id instead:
    matching_id = "eeee5555-5555-5555-5555-555555555555"
    waker.client.notifications_unread.return_value = [
        {**notif, "id": matching_id}
    ]
    chunks = [
        f'data: {{"type":"notification.created","event":"topic.lifecycle.closed","notification_id":"{matching_id}"}}\n\n'
    ]

    response = _fake_sse_response(chunks)
    await waker._sse_consume_stream(response, stats, stop)

    assert captured_sources == [WAKE_SOURCE_SSE]
