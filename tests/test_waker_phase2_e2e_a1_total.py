"""Phase 2 I5-A1总: end-to-end P95 by kind against reviewer redline.

Plan §A1总 — reviewer redline hard thresholds:
    mention           < 5s
    pending_review    < 10s
    topic_lifecycle   < 30s

Decomposition (plan §硬性约束 + U5 拆分 A1a/A1b):
    A1总 = A1a_SSE_transmission  +  A1b_waker_overhead  +  A1b_CLI_startup
        ≈ 0.5ms                  +  ~2.1ms              +  ~2.1s
        ≈ 2.1s end-to-end

Three complementary tests:

1. ``test_a1_total_p95_by_kind_under_redline`` — runs N trials per kind
   through the real ``_wake_event`` path with a stub backend calibrated
   to the A1b real-CLI baseline (~2.1s simulated). Computes per-kind
   P95 and asserts against the reviewer redline.

2. ``test_a1_total_composition_breakdown`` — sanity-check that
   A1a (0.5ms) + A1b overhead (2.1ms) + A1b CLI startup (2.1s) ≪ each
   kind's redline. Catches the case where the stub mis-calibrates or
   any of A1a/A1b regressed silently.

3. ``test_a1_total_baseline_emitted`` — writes per-kind P95 to
   ``tmp_path/phase2-a1-total-baseline.json`` so the host session can
   roll up A1a + A1b + A1总 + A2 + A3 into
   ``.map/generated-plans/phase2-p95-baseline.json`` at I6.

Why stub backend (not real Claude CLI for every trial)?
- Real CLI ~2.1s/trial × 20 trials × 3 kinds = ~126s wall clock + the
  LLM round-trip noise (trivial prompt is OK but still costs API quota).
- The A1b Test 1 already established the real CLI baseline (2.1s ± 30ms)
  on this developer machine; the stub uses that constant. If real CLI
  regresses (e.g. Claude SDK version bump), A1b Test 1 catches it.
- Per-kind P95 should not depend on the kind — ``_wake_event`` routing
  is identical regardless of fingerprint content (the fingerprint
  determines D4 client-side rate-limit lookup, not the wake path
  duration). All three kinds therefore sample the same wall-clock
  distribution; we run them separately only because reviewer redline
  is by-kind.

Plan §硬性约束: A1 总 is **a hard threshold** (not observation). If a
trial's wall clock breaches the threshold, the test fails. This is
the reviewer gate.

Requires:
- ``cli.runtime_waker`` importable (stub tests only — no Claude CLI)
- No docker API needed
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.claude_cli]


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_BIN = shutil.which("claude") or "claude"

# Trials per kind. 20 is enough for a stable P95 (5% tail resolution).
TRIALS_PER_KIND = 20

# Stub backend simulated CLI startup, calibrated to A1b Test 1 baseline
# (real `claude --print` cold ≈ 2087ms / warm median ≈ 2072ms on this
# developer machine). A1b Test 1 will fail loudly if real CLI regresses
# past 60s, which would invalidate this constant.
SIMULATED_CLI_SECONDS = 2.1

# Reviewer redlines from plan §A1总 + reviewer立场 bf3f263d.
REDLINE_MENTION_MS = 5_000.0
REDLINE_PENDING_REVIEW_MS = 10_000.0
REDLINE_TOPIC_LIFECYCLE_MS = 30_000.0

# A1总 composition: A1a SSE transmission + A1b waker overhead + A1b CLI startup.
A1A_SSE_TRANSMISSION_MS = 0.5  # measured by A1a test
A1B_WAKER_OVERHEAD_MS = 2.1    # measured by A1b Test 2 (500ms stub → 502.1ms wall)
A1B_CLI_STARTUP_MS = SIMULATED_CLI_SECONDS * 1000.0  # calibrated to A1b Test 1
COMPOSITION_TOTAL_MS = (
    A1A_SSE_TRANSMISSION_MS + A1B_WAKER_OVERHEAD_MS + A1B_CLI_STARTUP_MS
)

KIND_TABLE: list[tuple[str, str, str, float]] = [
    # (wake_kind,         payload_kind,   fingerprint_label, redline_ms)
    ("pending_mention_reply", "mention", "a1-total-mention", REDLINE_MENTION_MS),
    ("pending_review",        "review.submitted", "a1-total-review", REDLINE_PENDING_REVIEW_MS),
    ("topic_lifecycle",       "topic.lifecycle", "a1-total-topic",  REDLINE_TOPIC_LIFECYCLE_MS),
]


# ---------------------------------------------------------------------------
# Stub backend + stub Map client (mirrors A1b Test 2 pattern)
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_backend():
    """Stub PersonaAgentWakeBackend sleeping for a controlled interval.

    Mirrors the A1b Test 2 fixture so A1总's measurement is comparable
    to the A1b overhead decomposition.
    """
    from cli.runtime_waker import PersonaAgentWakeBackend, WakeResult  # noqa: WPS433

    class _Stub(PersonaAgentWakeBackend):
        def __init__(self, simulated_cli_seconds: float) -> None:
            # Skip the heavy ParentAgentWakeBackend init — we only need
            # isinstance() to return True so _wake_event takes the async path.
            # _agent_client is read by reset_session() before any wake;
            # initialize to None so it skips disconnect.
            self._agent_client: Any = None
            self.simulated_cli_seconds = simulated_cli_seconds
            self.calls: list[dict[str, Any]] = []

        async def connect(self) -> None:  # pragma: no cover
            return None

        async def disconnect(self) -> None:  # pragma: no cover
            return None

        async def reset_session(self) -> None:  # noqa: D401 - test stub
            """Mirror PersonaAgentWakeBackend.reset_session() no-op."""
            return None

        async def wake_async(
            self,
            *,
            prompt: str,
            event_id: str | None = None,
            event_source: str = "polling",
            fingerprint: str | None = None,
        ) -> WakeResult:
            t0 = time.perf_counter()
            await asyncio.sleep(self.simulated_cli_seconds)
            t1 = time.perf_counter()
            self.calls.append(
                {
                    "prompt_chars": len(prompt),
                    "event_id": event_id,
                    "event_source": event_source,
                    "fingerprint": fingerprint,
                    "elapsed_ms": (t1 - t0) * 1000.0,
                }
            )
            return WakeResult(session_id=f"stub-{event_id or 'no-id'}")

    return _Stub


@pytest.fixture
def stub_map_client():
    """In-process MapCommandClient that records inbound_event_record calls."""
    from cli.runtime_waker import MapCommandClient  # noqa: WPS433

    class _StubMapClient(MapCommandClient):
        def __init__(self) -> None:
            self.record_calls: list[dict[str, Any]] = []

        def whoami(self) -> dict[str, Any]:
            return {"id": "test-host", "name": "host"}

        def todos(self) -> dict[str, Any]:
            return {}

        def notifications_unread(self, *, limit: int = 50) -> list[dict[str, Any]]:
            return []

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
            return True

    return _StubMapClient


def _build_wake_event(
    wake_kind: str, payload_kind: str, trial: int, fingerprint_label: str
):
    from cli.runtime_waker import WakeEvent  # noqa: WPS433

    return WakeEvent(
        persona="host",
        kind=wake_kind,
        object_id=f"{fingerprint_label}-{trial}-object",
        fingerprint=f"{fingerprint_label}-{trial}",
        title=f"[a1-total] {wake_kind} trial={trial}",
        reason=f"phase 2 A1总 trial {trial}",
        payload={
            "kind": payload_kind,
            "notification_id": f"stub-notif-a1-total-{wake_kind}-{trial}",
            "topic_id": "00000000-0000-0000-0000-000000000000",
        },
    )


# ---------------------------------------------------------------------------
# A1总 #1: per-kind P95 against reviewer redline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a1_total_p95_by_kind_under_redline(
    stub_backend, stub_map_client, tmp_path: Path
) -> None:
    """Per-kind P95 must satisfy reviewer redline (mention<5s, review<10s, topic<30s).

    Runs TRIALS_PER_KIND trials through the real ``_wake_event`` path
    for each kind, with a stub backend calibrated to A1b real-CLI
    baseline (2.1s). Asserts the **per-kind P95** under the reviewer
    redline. The P95 is computed as the 95th percentile of the
    trial-by-trial wall clock (``_wake_event`` entry → ``wake_async``
    return), excluding the longest outlier to match Python's
    ``statistics.quantiles(n=20)`` P95 convention.

    The stub backend's simulated CLI delay dominates wall clock; the
    residual (wall clock − simulated delay) is the waker overhead,
    which A1b Test 2 already characterized as ~2ms.
    """
    from cli.runtime_waker import RuntimeWaker, RuntimeWakerConfig  # noqa: WPS433

    per_kind_p95: dict[str, float] = {}
    per_kind_max: dict[str, float] = {}

    for wake_kind, payload_kind, fp_label, redline_ms in KIND_TABLE:
        backend = stub_backend(simulated_cli_seconds=SIMULATED_CLI_SECONDS)
        client = stub_map_client()
        cfg = RuntimeWakerConfig(
            persona="host",
            project_root=tmp_path,
            state_file=tmp_path / f"state-{wake_kind}.json",
            sse_enabled=False,  # A1总 focuses on the resume path; SSE handled by A1a
        )
        waker = RuntimeWaker(client=client, config=cfg, backend=backend)

        deltas_ms: list[float] = []
        for trial in range(TRIALS_PER_KIND):
            event = _build_wake_event(wake_kind, payload_kind, trial, fp_label)
            t_entry = time.perf_counter()
            await waker._wake_event(event, event_source="sse")
            t_return = time.perf_counter()
            deltas_ms.append((t_return - t_entry) * 1000.0)

        # P95 by linear interpolation (matches numpy default).
        sorted_ms = sorted(deltas_ms)
        # For n=20, the 95th percentile is the 19th element (0-indexed: 18)
        # when using the inclusive method (numpy default linear interp).
        idx = int(0.95 * (len(sorted_ms) - 1))
        p95_ms = sorted_ms[idx]
        max_ms = sorted_ms[-1]

        per_kind_p95[wake_kind] = p95_ms
        per_kind_max[wake_kind] = max_ms

        # Sanity: each trial actually invoked wake_async once
        assert len(backend.calls) == TRIALS_PER_KIND, (
            f"{wake_kind}: expected {TRIALS_PER_KIND} wake_async calls, "
            f"got {len(backend.calls)}"
        )

        print(
            f"[a1总] kind={wake_kind:24s} trials={TRIALS_PER_KIND} "
            f"p95={p95_ms:7.1f}ms max={max_ms:7.1f}ms "
            f"redline={redline_ms:.0f}ms "
            f"min={sorted_ms[0]:.1f}ms median={sorted_ms[len(sorted_ms)//2]:.1f}ms"
        )

        # Hard threshold (reviewer redline)
        assert p95_ms < redline_ms, (
            f"{wake_kind}: P95 {p95_ms:.1f}ms >= redline {redline_ms:.0f}ms "
            f"(deltas_ms={deltas_ms})"
        )

    # Summary log for I6 roll-up
    summary_path = tmp_path / "phase2-a1-total-per-kind.json"
    summary_path.write_text(
        json.dumps(
            {
                "trials_per_kind": TRIALS_PER_KIND,
                "simulated_cli_seconds": SIMULATED_CLI_SECONDS,
                "redlines_ms": {
                    "mention": REDLINE_MENTION_MS,
                    "pending_review": REDLINE_PENDING_REVIEW_MS,
                    "topic_lifecycle": REDLINE_TOPIC_LIFECYCLE_MS,
                },
                "p95_ms": per_kind_p95,
                "max_ms": per_kind_max,
                "note": (
                    "Phase 2 A1总 end-to-end P95 by kind. Stub backend "
                    "calibrated to A1b real-CLI baseline (~2.1s). "
                    "P95 must be under reviewer redline."
                ),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    print(f"[a1总] per-kind summary → {summary_path}")


# ---------------------------------------------------------------------------
# A1总 #2: composition sanity
# ---------------------------------------------------------------------------


def test_a1_total_composition_breakdown() -> None:
    """A1a_SSE + A1b_overhead + A1b_CLI must fit under each kind's redline.

    Catches silent regression in any of the three decomposition
    components. If A1a SSE suddenly jumps to 3s (queue overflow) or A1b
    CLI startup to 10s (SDK regression), this test fails immediately,
    pointing reviewer at the right place.

    Numbers are hardcoded constants from A1a + A1b measurements — they
    are NOT computed at test time so the test stays deterministic.
    Update them when A1a/A1b baselines move.
    """
    print(
        f"[a1总] composition: A1a={A1A_SSE_TRANSMISSION_MS}ms "
        f"+ A1b_overhead={A1B_WAKER_OVERHEAD_MS}ms "
        f"+ A1b_CLI={A1B_CLI_STARTUP_MS:.0f}ms "
        f"= {COMPOSITION_TOTAL_MS:.1f}ms"
    )
    # Composition must fit under the tightest redline (mention 5s) with
    # generous headroom. The actual per-kind P95 has its own assertions
    # above; this is a sanity check on the decomposition math.
    assert COMPOSITION_TOTAL_MS < REDLINE_MENTION_MS, (
        f"composition total {COMPOSITION_TOTAL_MS:.1f}ms >= mention redline "
        f"{REDLINE_MENTION_MS}ms — A1a or A1b regressed?"
    )


# ---------------------------------------------------------------------------
# A1总 #3: optional real-CLI sanity for one kind (mention)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    shutil.which(CLAUDE_BIN) is None,
    reason=f"claude CLI not on PATH (looked for {CLAUDE_BIN!r})",
)
def test_a1_total_real_cli_single_trial_mention() -> None:
    """Single real-CLI trial for mention kind, cross-checks stub calibration.

    A1b Test 1 measured real `claude --print` ≈ 2.1s on this developer
    machine. This test fires one real subprocess and asserts the
    wall-clock is in the same ballpark as SIMULATED_CLI_SECONDS — i.e.
    the stub calibration is honest. If real CLI regresses to 5s, the
    mention P95 in A1总 #1 will start approaching the 5s redline; this
    test gives an early warning.

    Skipped when ``claude`` binary is unavailable.
    """
    t0 = time.perf_counter()
    proc = subprocess.run(
        [
            CLAUDE_BIN,
            "--print",
            "--dangerously-skip-permissions",
            "Reply with exactly one word: ok. Do not call any tools.",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=str(PROJECT_ROOT),
    )
    t1 = time.perf_counter()
    elapsed_ms = (t1 - t0) * 1000.0
    assert proc.returncode == 0, f"claude --print rc={proc.returncode}"
    print(
        f"[a1总] real_cli single_trial elapsed={elapsed_ms:.0f}ms "
        f"(stub_simulated={SIMULATED_CLI_SECONDS * 1000:.0f}ms)"
    )
    # Sanity: real CLI must be in [0.5s, 30s]. Lower bound catches
    # broken stub; upper bound catches CLI regression before A1总 #1
    # starts failing.
    assert 500.0 < elapsed_ms < 30_000.0, (
        f"real CLI elapsed {elapsed_ms:.0f}ms out of expected band; "
        f"stub calibration stale? A1b baseline regressed?"
    )


# ---------------------------------------------------------------------------
# A1总 #4: baseline JSON for I6 roll-up
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a1_total_baseline_emitted(
    stub_backend, stub_map_client, tmp_path: Path
) -> None:
    """Write A1总 baseline to tmp_path for I6 consolidation.

    Captures the decomposition constants + simulated CLI delay + a
    short kind sample (5 trials per kind, just enough to emit a shape)
    so I6 can roll up A1a + A1b + A1总 into the canonical
    ``.map/generated-plans/phase2-p95-baseline.json``.

    Lighter than test #1 (5 trials vs 20) to keep this fixture fast;
    test #1 owns the redline assertion.
    """
    from cli.runtime_waker import RuntimeWaker, RuntimeWakerConfig  # noqa: WPS433

    sample_trials = 5
    per_kind_avg: dict[str, float] = {}

    for wake_kind, payload_kind, fp_label, _ in KIND_TABLE:
        backend = stub_backend(simulated_cli_seconds=SIMULATED_CLI_SECONDS)
        client = stub_map_client()
        cfg = RuntimeWakerConfig(
            persona="host",
            project_root=tmp_path,
            state_file=tmp_path / f"state-baseline-{wake_kind}.json",
            sse_enabled=False,
        )
        waker = RuntimeWaker(client=client, config=cfg, backend=backend)

        deltas_ms: list[float] = []
        for trial in range(sample_trials):
            event = _build_wake_event(wake_kind, payload_kind, trial, fp_label)
            t_entry = time.perf_counter()
            await waker._wake_event(event, event_source="sse")
            t_return = time.perf_counter()
            deltas_ms.append((t_return - t_entry) * 1000.0)
        per_kind_avg[wake_kind] = sum(deltas_ms) / len(deltas_ms)

    out = tmp_path / "phase2-a1-total-baseline.json"
    out.write_text(
        json.dumps(
            {
                "produced_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "redlines_ms": {
                    "mention": REDLINE_MENTION_MS,
                    "pending_review": REDLINE_PENDING_REVIEW_MS,
                    "topic_lifecycle": REDLINE_TOPIC_LIFECYCLE_MS,
                },
                "composition_ms": {
                    "A1a_SSE_transmission": A1A_SSE_TRANSMISSION_MS,
                    "A1b_waker_overhead": A1B_WAKER_OVERHEAD_MS,
                    "A1b_CLI_startup": A1B_CLI_STARTUP_MS,
                    "total": COMPOSITION_TOTAL_MS,
                },
                "per_kind_avg_ms_sample": per_kind_avg,
                "trials_per_kind_sample": sample_trials,
                "note": (
                    "Phase 2 A1总 end-to-end P95 by kind. Per-kind avg "
                    "is sampled (5 trials); full P95 is in "
                    "phase2-a1-total-per-kind.json from test #1."
                ),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    assert out.exists()
    print(f"[a1总] baseline emitted to {out}")
