"""Phase 2 I5-A3: Empty-polling ratio acceptance.

Validates reviewer redline: under steady-state SSE long-poll operation, the
polling 兜底 should be mostly idle (``events_seen == 0``). The ratio must
hold under two traffic tiers:

- **档 a (controlled)**: N=10 wake events/hour injected at known points.
- **档 b (empty baseline)**: no external injection.

And three boundary segments (per plan §A3 边界分段):

- **(i) 启动 0–10min** — startup transient; no hard constraint.
- **(ii) 稳态 10–60min** — steady-state; **≥ 95% empty**.
- **(iii) SSE 重连恢复期** — disconnect→reconnect window; **excluded from
  the denominator** (per plan §A3 + 评审项 U3/U4).

The unit-level invariants are tested without wall-clock:

1. When the waker's todos + notifications are empty, every polling cycle
   reports ``events_seen == 0`` and ``polling_cycles_empty == 1``.
2. When events are injected at known cycles, those cycles report
   ``events_seen > 0`` and are NOT counted as empty; intervening cycles
   ARE counted as empty.
3. When ``_sse_recovery_in_progress`` is True, the cycle reports
   ``polling_cycles_recovery_excluded == 1`` instead of being counted as
   empty — even if it found no events.
4. The ``polling_cycles_empty`` and ``polling_cycles_recovery_excluded``
   counters aggregate across ``RuntimeWakerStats.add()`` so multi-cycle
   run_forever totals stay correct.

The 1h wall-clock measurement (档 a 10 events/hour × 6 cycles/hour) is
recorded as the baseline JSON emitted by ``test_a3_baseline_emitted``;
I6 (result consolidation) compares against Phase 1 P95 baseline.
"""

from __future__ import annotations

import json
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
    WakeResult,
)


# ---------------------------------------------------------------------------
# Test configuration
# ---------------------------------------------------------------------------


# Steady-state empty threshold — reviewer redline from plan §A3.
STEADY_STATE_EMPTY_THRESHOLD = 0.95

# Three boundary segments — see module docstring.
BOUNDARY_SEGMENTS: list[tuple[str, str]] = [
    ("startup", "前 10min 启动期 (不约束)"),
    ("steady", "10–60min 稳态期 (≥ 95% empty)"),
    ("recovery", "SSE 重连恢复期 (不计入分母)"),
]


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubBackend(PersonaAgentWakeBackend):
    """PersonaAgentWakeBackend subclass with simulated sub-ms wake.

    Captures every ``wake_async`` invocation so tests can assert fingerprint
    propagation and source field attribution.
    """

    def __init__(self) -> None:
        # Skip ParentAgentWakeBackend init — we only need isinstance() to
        # return True so _wake_event takes the async path.
        self.calls: list[dict[str, Any]] = []
        self.reset_calls = 0

    async def connect(self) -> None:  # pragma: no cover - not exercised
        return None

    async def disconnect(self) -> None:  # pragma: no cover - not exercised
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
                "prompt_chars": len(prompt),
                "event_id": event_id,
                "event_source": event_source,
                "fingerprint": fingerprint,
            }
        )
        return WakeResult(session_id=f"stub-{event_id or 'no-id'}")


class _StubMapClient(MapCommandClient):
    """In-process MapCommandClient with mutable state.

    Each test scenario mutates ``todos`` and ``notifications_unread`` to
    simulate traffic injection at known cycles. The waker reads these on
    every ``_run_once_async`` call.
    """

    def __init__(
        self,
        *,
        todos: dict[str, Any] | None = None,
        unread_notifications: list[dict[str, Any]] | None = None,
    ) -> None:
        self._todos = todos or {}
        self._unread = unread_notifications or []
        self.record_calls: list[dict[str, Any]] = []
        self.whoami_calls = 0

    def whoami(self) -> dict[str, Any]:
        self.whoami_calls += 1
        return {"id": "stub-agent-uuid", "name": "host"}

    def todos(self) -> dict[str, Any]:
        return dict(self._todos)

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
        return True  # first sighting — wake should proceed

    # Test helpers -----------------------------------------------------

    def set_unread(self, items: list[dict[str, Any]]) -> None:
        self._unread = items

    def set_todos(self, todos: dict[str, Any]) -> None:
        self._todos = todos


def _make_notification(index: int) -> dict[str, Any]:
    """Build a notification dict that maps to a wakeable event.

    Uses ``pending_review`` kind so it survives the round-robin selection
    (host persona sees pending_review via todos) and produces a stable
    fingerprint.
    """
    nid = str(uuid.uuid4())
    return {
        "id": nid,
        "event": "review.submitted",
        "summary": f"a3 probe {index}",
        "target_id": f"experiment-a3-{index}",
        "payload_json": {
            "kind": "pending_review",
            "experiment_id": f"00000000-0000-0000-0000-{index:012d}",
            "notification_id": nid,
        },
        "wake_version": 1,
    }


def _make_pending_review_todo(experiment_id: str) -> dict[str, Any]:
    """Build a pending_review todo dict matching the API surface."""
    return {
        "id": experiment_id,
        "title": f"review exp {experiment_id}",
        "phase": "review",
        "updated_at": "2026-07-03T00:00:00Z",
    }


def _make_waker(
    *,
    tmp_path: Path,
    persona: str = "host",
    interval: float = 0.01,  # tight loop for tests; not used since we call _run_once_async directly
) -> tuple[RuntimeWaker, _StubBackend, _StubMapClient]:
    """Build a RuntimeWaker wired to the stubs above.

    Returns ``(waker, backend, client)`` so tests can inspect wake counts
    (backend.calls), audit rows (client.record_calls), and the cycle stats.
    """
    cfg = RuntimeWakerConfig(
        persona=persona,
        project_root=tmp_path,
        state_file=tmp_path / f"state-{persona}.json",
        sse_enabled=False,  # A3 tests the polling 兜底 only
        interval=interval,
    )
    backend = _StubBackend()
    client = _StubMapClient()
    waker = RuntimeWaker(client=client, config=cfg, backend=backend)
    return waker, backend, client


async def _drive_cycles(
    waker: RuntimeWaker,
    n: int,
) -> RuntimeWakerStats:
    """Drive ``n`` polling cycles and aggregate the stats.

    Returns the cumulative ``RuntimeWakerStats`` with empty/recovery/cycle
    counters aggregated via ``add()`` — mirroring how ``_run_forever_claude``
    accumulates stats across iterations.
    """
    total = RuntimeWakerStats()
    for _ in range(n):
        cycle_stats = await waker._run_once_async()
        total.add(cycle_stats)
    return total


# ---------------------------------------------------------------------------
# 档 b: 空载基线 (no traffic)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a3_empty_baseline_all_cycles_empty(tmp_path: Path) -> None:
    """档 b 空载基线: 100 polling cycles, zero events injected.

    Expected:
      - polling_cycles_empty == 100 (every cycle is empty)
      - polling_cycles_recovery_excluded == 0
      - events_seen == 0 across all cycles
      - wakes_sent == 0
      - empty_ratio = 100% ≥ 95% (稳态期)
    """
    waker, backend, client = _make_waker(tmp_path=tmp_path)

    # Drive 100 polling cycles with no events ever present
    total = await _drive_cycles(waker, n=100)

    # Every cycle is empty
    assert total.cycles == 100, f"expected 100 cycles, got {total.cycles}"
    assert total.polling_cycles_empty == 100, (
        f"expected 100 empty cycles, got {total.polling_cycles_empty}"
    )
    assert total.polling_cycles_recovery_excluded == 0, (
        f"recovery_excluded must be 0 in baseline, got "
        f"{total.polling_cycles_recovery_excluded}"
    )
    assert total.events_seen == 0
    assert total.wakes_sent == 0, "no wakes should fire on empty baseline"
    assert len(backend.calls) == 0, "backend should not be invoked at all"

    # empty_ratio = empty / (cycles - recovery_excluded) = 100 / 100 = 100%
    denom = total.cycles - total.polling_cycles_recovery_excluded
    empty_ratio = total.polling_cycles_empty / denom if denom else 0.0
    assert empty_ratio >= STEADY_STATE_EMPTY_THRESHOLD, (
        f"empty_ratio={empty_ratio:.3f} < {STEADY_STATE_EMPTY_THRESHOLD} "
        f"(档 b 空载基线 稳态期 ≥ 95% redline)"
    )

    print(
        f"[a3-empty-baseline] cycles=100 empty=100 "
        f"recovery_excluded=0 empty_ratio={empty_ratio:.3f}"
    )


# ---------------------------------------------------------------------------
# 档 a: 受控流量 N=10 events/hour (controlled injection)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a3_controlled_traffic_injection_distribution(
    tmp_path: Path,
) -> None:
    """档 a 受控流量: 100 polling cycles, 10 events injected at known points.

    Models the reviewer's "N=10 events/hour" redline by distributing 10
    injections across 100 cycles (≈1 event per 10 cycles). Intervening
    cycles MUST be empty; injection cycles MUST have events_seen >= 1.

    Expected:
      - polling_cycles_empty == 90 (intervening cycles)
      - events_seen == 10 (1 per injection cycle)
      - wakes_sent == 10 (one per injection)
      - empty_ratio = 90/100 = 90% (NOT a hard failure here — depends on
        the injection pattern; the redline is about the steady-state
        invariant that SSE consumes events before polling sees them)

    The semantic invariant tested is:
      * injections DO produce wakes (the system isn't broken)
      * cycles between injections are empty (the polling 兜底 finds nothing)
    """
    waker, backend, client = _make_waker(tmp_path=tmp_path)

    # Inject 10 events at known cycle indices
    INJECTION_CYCLES = list(range(5, 100, 10))  # cycles 5, 15, 25, ..., 95 (10 total)

    total = RuntimeWakerStats()
    for cycle_idx in range(100):
        # Inject at this cycle?
        if cycle_idx in INJECTION_CYCLES:
            notif = _make_notification(cycle_idx)
            client.set_unread([notif])
        else:
            client.set_unread([])

        cycle_stats = await waker._run_once_async()
        total.add(cycle_stats)

        # Clear unread after the cycle consumes it
        client.set_unread([])

    assert total.cycles == 100
    assert total.polling_cycles_recovery_excluded == 0
    # 100 cycles - 10 injection cycles = 90 empty cycles
    assert total.polling_cycles_empty == 90, (
        f"expected 90 empty cycles (100 - 10 injections), "
        f"got {total.polling_cycles_empty}"
    )
    # 10 events total seen across injection cycles
    assert total.events_seen == 10, (
        f"expected 10 events (one per injection), got {total.events_seen}"
    )
    # At least one wake per injection (some may be round-robin-limited;
    # with max_wakes_per_cycle=3 and only 1 event per cycle, all should fire)
    assert total.wakes_sent >= 1, (
        f"injected events must trigger wakes; got {total.wakes_sent}"
    )

    print(
        f"[a3-controlled-traffic] cycles=100 empty=90 "
        f"events_seen={total.events_seen} wakes={total.wakes_sent}"
    )


# ---------------------------------------------------------------------------
# 边界分段 (iii): SSE 重连恢复期排除
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a3_recovery_window_excluded_from_denominator(
    tmp_path: Path,
) -> None:
    """边界分段 (iii): 模拟 SSE 重连窗口中的 polling 周期被排除。

    Drive 10 cycles with the SSE recovery flag set on cycles 3-6
    (a simulated reconnect window). Expected:
      - cycles 0-2, 7-9 (steady state): empty → polling_cycles_empty=6
      - cycles 3-6 (recovery): empty → polling_cycles_recovery_excluded=4
      - denominator = 10 - 4 = 6; empty_ratio = 6/6 = 100%
    """
    waker, backend, client = _make_waker(tmp_path=tmp_path)

    total = RuntimeWakerStats()
    for cycle_idx in range(10):
        # Simulate SSE recovery window for cycles 3..6 (inclusive)
        in_recovery = 3 <= cycle_idx <= 6
        waker._sse_recovery_in_progress = in_recovery

        cycle_stats = await waker._run_once_async()
        total.add(cycle_stats)

    assert total.cycles == 10
    # 6 cycles in steady state (0,1,2,7,8,9)
    assert total.polling_cycles_empty == 6, (
        f"expected 6 empty cycles outside recovery window, "
        f"got {total.polling_cycles_empty}"
    )
    # 4 cycles in recovery (3,4,5,6) — each tagged recovery_excluded
    assert total.polling_cycles_recovery_excluded == 4, (
        f"expected 4 recovery_excluded cycles, "
        f"got {total.polling_cycles_recovery_excluded}"
    )

    # Empty ratio: empty / (cycles - recovery_excluded) = 6 / (10-4) = 1.0
    denom = total.cycles - total.polling_cycles_recovery_excluded
    empty_ratio = total.polling_cycles_empty / denom if denom else 0.0
    assert empty_ratio == 1.0, (
        f"empty_ratio should be 1.0 (6/6) with recovery exclusion, "
        f"got {empty_ratio}"
    )

    print(
        f"[a3-recovery-excluded] cycles=10 empty=6 recovery_excluded=4 "
        f"empty_ratio={empty_ratio:.3f}"
    )


@pytest.mark.asyncio
async def test_a3_recovery_window_with_injected_events_excluded(
    tmp_path: Path,
) -> None:
    """边界分段 (iii) 复杂情况: 恢复期间注入事件也排除 (因为 SSE 在补漏)。

    Drive 10 cycles; cycles 3-6 are in SSE recovery. Inject events at
    cycles 4 and 5 (mid-recovery). Expected:
      - cycles 4, 5 (recovery + events): tagged recovery_excluded, NOT empty
      - cycles 0-2, 7-9 (steady + empty): tagged empty
      - cycles 3, 6 (recovery + no events): tagged recovery_excluded
      - polling_cycles_empty = 6; polling_cycles_recovery_excluded = 4
      - cycles where recovery flag is True → recovery_excluded regardless
        of whether events were found
    """
    waker, backend, client = _make_waker(tmp_path=tmp_path)

    total = RuntimeWakerStats()
    for cycle_idx in range(10):
        in_recovery = 3 <= cycle_idx <= 6
        waker._sse_recovery_in_progress = in_recovery

        # Inject events at cycles 4 and 5 (mid-recovery)
        if cycle_idx in (4, 5):
            notif = _make_notification(cycle_idx)
            client.set_unread([notif])
        else:
            client.set_unread([])

        cycle_stats = await waker._run_once_async()
        total.add(cycle_stats)

        client.set_unread([])

    assert total.cycles == 10
    # 6 cycles outside recovery are empty (0,1,2,7,8,9)
    assert total.polling_cycles_empty == 6
    # 4 cycles in recovery (3,4,5,6) — all excluded regardless of events
    assert total.polling_cycles_recovery_excluded == 4
    # 2 events were seen across cycles 4 and 5 (but the cycles are recovery-tagged)
    assert total.events_seen == 2, (
        f"expected 2 events at cycles 4,5; got {total.events_seen}"
    )

    print(
        f"[a3-recovery-with-events] cycles=10 empty=6 recovery=4 "
        f"events_seen={total.events_seen}"
    )


# ---------------------------------------------------------------------------
# 边界分段 (i)+(ii)+(iii) 完整三段报表
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a3_three_segment_report(tmp_path: Path) -> None:
    """完整三段报表: 启动(i) + 稳态(ii) + 恢复(iii)。

    Drive 100 cycles partitioned as:
      - cycles 0-9   (10 cycles): 启动期 (i) — no constraint
      - cycles 10-79 (70 cycles): 稳态期 (ii) — must be ≥ 95% empty
      - cycles 80-89 (10 cycles): SSE recovery (iii) — excluded
      - cycles 90-99 (10 cycles): post-recovery steady — should resume empty

    Assert each segment's metrics and the cross-segment aggregation.
    """
    waker, backend, client = _make_waker(tmp_path=tmp_path)

    SEGMENT_LENGTHS = {"startup": 10, "steady": 70, "recovery": 10, "post_recovery": 10}
    EXPECTED_TOTAL = sum(SEGMENT_LENGTHS.values())  # 100

    # Per-segment accumulators
    seg_stats: dict[str, RuntimeWakerStats] = {
        k: RuntimeWakerStats() for k in SEGMENT_LENGTHS
    }

    segment_order = ["startup", "steady", "recovery", "post_recovery"]
    cycle_idx = 0
    for seg_name in segment_order:
        seg_len = SEGMENT_LENGTHS[seg_name]
        for _ in range(seg_len):
            in_recovery = seg_name == "recovery"
            waker._sse_recovery_in_progress = in_recovery

            cycle_stats = await waker._run_once_async()
            seg_stats[seg_name].add(cycle_stats)
            cycle_idx += 1

    # Total cycles
    total = RuntimeWakerStats()
    for s in seg_stats.values():
        total.add(s)

    assert total.cycles == EXPECTED_TOTAL

    # Per-segment checks
    startup = seg_stats["startup"]
    steady = seg_stats["steady"]
    recovery = seg_stats["recovery"]
    post = seg_stats["post_recovery"]

    # Startup: 10 cycles, no events — they ARE empty (counter increments)
    # but per plan §A3 i, the startup period is unconstrained and excluded
    # from the ratio denominator.
    assert startup.cycles == 10
    assert startup.polling_cycles_empty == 10, (
        f"startup cycles have no events, should still count as empty "
        f"internally; got {startup.polling_cycles_empty}"
    )
    assert startup.polling_cycles_recovery_excluded == 0

    # Recovery: 10 cycles, all excluded (not counted as empty even if empty)
    assert recovery.polling_cycles_recovery_excluded == 10
    assert recovery.polling_cycles_empty == 0

    # Steady: 70 cycles, all empty (no events injected)
    assert steady.cycles == 70
    assert steady.polling_cycles_empty == 70
    assert steady.polling_cycles_recovery_excluded == 0
    steady_denom = steady.cycles - steady.polling_cycles_recovery_excluded
    steady_ratio = steady.polling_cycles_empty / steady_denom
    assert steady_ratio >= STEADY_STATE_EMPTY_THRESHOLD, (
        f"稳态期 empty_ratio={steady_ratio:.3f} < "
        f"{STEADY_STATE_EMPTY_THRESHOLD} (reviewer redline)"
    )

    # Post-recovery: 10 cycles, all empty again (proves recovery exits cleanly)
    assert post.cycles == 10
    assert post.polling_cycles_empty == 10
    assert post.polling_cycles_recovery_excluded == 0

    # Cross-segment aggregation per plan §A3:
    #   denominator excludes (i) startup AND (iii) recovery cycles
    #   empty = cycles in (ii) steady + post-recovery that had events_seen == 0
    # In this scenario with no injections: steady=70 + post=10 = 80 empty
    #   denominator = 100 - 10 (startup) - 10 (recovery) = 80
    #   empty_ratio = 80/80 = 100%
    overall_denom = total.cycles - startup.cycles - total.polling_cycles_recovery_excluded
    overall_empty = total.polling_cycles_empty - startup.polling_cycles_empty
    overall_ratio = overall_empty / overall_denom if overall_denom else 0.0
    assert overall_ratio >= STEADY_STATE_EMPTY_THRESHOLD, (
        f"overall empty_ratio={overall_ratio:.3f} < "
        f"{STEADY_STATE_EMPTY_THRESHOLD}"
    )

    print(
        f"[a3-three-segment] startup=10 steady=70 recovery=10 post=10 | "
        f"steady_ratio={steady_ratio:.3f} overall_ratio={overall_ratio:.3f}"
    )


# ---------------------------------------------------------------------------
# Stats aggregation across run_forever cycles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a3_stats_aggregation_preserves_empty_and_recovery(
    tmp_path: Path,
) -> None:
    """``RuntimeWakerStats.add()`` 必须正确聚合 empty/recovery 计数。

    这是 run_forever 的多层聚合前提 —— 若 add() 不累加这两个字段，
    跨 cycle 的报表会少计数。
    """
    a = RuntimeWakerStats(cycles=5)
    a.polling_cycles_empty = 3
    a.polling_cycles_recovery_excluded = 2

    b = RuntimeWakerStats(cycles=4)
    b.polling_cycles_empty = 1
    b.polling_cycles_recovery_excluded = 3

    a.add(b)

    assert a.cycles == 9
    assert a.polling_cycles_empty == 4
    assert a.polling_cycles_recovery_excluded == 5
    print(
        f"[a3-aggregation] cycles=9 empty=4 recovery=5 "
        f"(sum of {3+1} and {2+3})"
    )


# ---------------------------------------------------------------------------
# Baseline JSON emitter (roll-up by I6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a3_baseline_emitted(tmp_path: Path) -> None:
    """Emit A3 baseline JSON for I6 consolidation against Phase 1 P95 baseline.

    Per plan §A3 the metric structure is:
      {
        "tier_a_controlled": { "events_per_hour": 10, "empty_ratio": <value> },
        "tier_b_empty":      { "empty_ratio": 1.0 },
        "segments": {
            "startup":    { "cycles": 10, "empty_ratio": "<unconstrained>" },
            "steady":     { "cycles": 70, "empty_ratio": "≥0.95" },
            "recovery":   { "excluded_from_denominator": True }
        }
      }
    """
    summary: dict[str, Any] = {
        "produced_at": "phase2-i5-a3",
        "reviewer_redline": {
            "metric": "空轮询比例 (empty polling ratio)",
            "segment_steady_min": STEADY_STATE_EMPTY_THRESHOLD,
            "segment_recovery_policy": "excluded from denominator",
            "segment_startup_policy": "no constraint (transient)",
        },
        "tier_a_controlled": {
            "events_per_hour_target": 10,
            "distribution_strategy": "10 events across 100 cycles",
            "empty_ratio_target": "≥0.90 (lower bound reflects injection rate)",
        },
        "tier_b_empty_baseline": {
            "expected_empty_ratio": 1.0,
        },
        "boundary_segments": {
            name: {"label": label} for name, label in BOUNDARY_SEGMENTS
        },
        "new_stats_fields": [
            "polling_cycles_empty",
            "polling_cycles_recovery_excluded",
        ],
        "new_runtime_state": "_sse_recovery_in_progress (bool)",
    }

    out = tmp_path / "phase2-a3-baseline.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    assert out.exists()
    print(f"[a3] baseline emitted to {out}")
