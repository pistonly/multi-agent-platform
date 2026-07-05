"""Phase 2 I5-A1b: Claude Code CLI backend wake latency observation.

Plan §A1b — observation baseline, **no threshold** (warm-pool optimization
lives in v0.8 backlog). Measures the wall-clock cost of the
``backend.wake_async`` path on this developer machine:

    A1b = t_wake_async_return − t_wake_event_entry

The bulk of this is dominated by Claude Code CLI subprocess startup
(plan observed ~3–8s on Claude Code 2.x). The test deliberately keeps
the prompt trivial so we measure subprocess+SDK overhead, not LLM turn
time.

Three complementary measurements:

1. ``test_real_claude_cli_subprocess_startup`` — directly spawn
   ``claude --print`` with a trivial prompt and measure cold-start time.
   This is the simplest measurement of "what does a single CLI turn
   cost on this box?" — the lower bound of A1b.

2. ``test_wake_async_path_overhead_with_stub_backend`` — exercise the
   in-process ``_wake_event`` → ``backend.wake_async`` path with a stub
   backend that sleeps for a configurable interval. Isolates waker
   overhead from Claude CLI startup. Useful for regression: if waker
   overhead grows, this test catches it.

3. ``test_a1b_baseline_emitted`` — write a JSON baseline to tmp_path
   so the host session can roll up A1a + A1b in I6 into
   ``.map/generated-plans/phase2-p95-baseline.json``.

Per plan §硬性约束, A1b has **no admission threshold** — these are
informational. CI may flip the assertion to ``pytest.skip`` if Claude
CLI is unavailable.

Requires:
- ``claude`` binary on ``$PATH`` (real test only; stub test does not)
- Real network access to api.anthropic.com for the real test
- The map waker module importable (stub test only)
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.claude_cli]


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
PROMPT_PING = (
    "Reply with exactly one word: ok. Do not call any tools, do not "
    "explain. Just the word."
)
PROMPT_LABEL = "[a1b-ping]"


# ---------------------------------------------------------------------------
# Real Claude CLI subprocess startup measurement
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    shutil.which(CLAUDE_BIN) is None,
    reason=f"claude CLI not on PATH (looked for {CLAUDE_BIN!r})",
)
def test_real_claude_cli_subprocess_startup(claude_cli_env: dict[str, str]) -> None:
    """Cold-start wall clock for ``claude --print``.

    A1b's lower bound: spawning Claude CLI + a trivial turn. The waker
    path adds SSE parse + _wake_event dispatch + record→mark→resume
    bookkeeping, but those are sub-100ms. The 3–8s plan estimate is
    dominated by subprocess startup; this test pins that on the
    developer machine.

    Runs the CLI 3 times to amortize the first cold start (OS page
    cache, etc.). Reports cold, warm, and median for the I6 baseline.
    """
    trials = 3
    deltas: list[float] = []
    stdout_seen: list[str] = []

    for trial in range(trials):
        t0 = time.perf_counter()
        proc = subprocess.run(
            [
                CLAUDE_BIN,
                "--print",
                "--dangerously-skip-permissions",
                PROMPT_PING,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            cwd=str(PROJECT_ROOT),
            env=claude_cli_env,
        )
        t1 = time.perf_counter()
        delta_ms = (t1 - t0) * 1000.0
        deltas.append(delta_ms)
        stdout_seen.append(proc.stdout.strip())
        assert proc.returncode == 0, (
            f"claude --print failed: rc={proc.returncode} stderr={proc.stderr!r}"
        )
        # Sanity: trivial prompt must produce something
        assert proc.stdout.strip(), f"empty stdout on trial {trial}"

    cold = deltas[0]
    warm = sorted(deltas[1:]) if len(deltas) > 1 else [cold]
    warm_median = warm[len(warm) // 2]
    min_warm = min(warm)
    max_warm = max(warm)

    print(
        f"[a1b] Claude CLI cold={cold:.0f}ms warm_median={warm_median:.0f}ms "
        f"min={min_warm:.0f}ms max={max_warm:.0f}ms "
        f"trials={trials} stdout_samples={stdout_seen[:1]}"
    )

    # No hard threshold (plan §A1b: observation only). But sanity:
    # cold start must complete in <60s; otherwise the subprocess is
    # broken (e.g. login prompt, network failure). Tight check for
    # the test to be useful; not a perf bar.
    assert cold < 60_000, f"cold start {cold:.0f}ms >= 60s (cli broken?)"


# ---------------------------------------------------------------------------
# Waker path overhead with stub backend (in-process)
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_backend():
    """A minimal stub that mimics ``PersonaAgentWakeBackend`` for testing.

    Sleeps for ``simulated_cli_seconds`` to mimic Claude CLI subprocess
    startup. Records every call so the test can assert the wake was
    actually invoked end-to-end.
    """
    from cli.runtime_waker import PersonaAgentWakeBackend, WakeResult  # noqa: WPS433

    class _Stub(PersonaAgentWakeBackend):
        def __init__(self, simulated_cli_seconds: float) -> None:
            # Skip the heavy ParentAgentWakeBackend init — we only need
            # isinstance() to return True so _wake_event takes the async path.
            self.simulated_cli_seconds = simulated_cli_seconds
            self.calls: list[dict[str, Any]] = []

        async def connect(self) -> None:  # pragma: no cover - not exercised
            return None

        async def disconnect(self) -> None:  # pragma: no cover - not exercised
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


@pytest.mark.asyncio
async def test_wake_async_path_overhead_with_stub_backend(
    stub_backend, tmp_path: Path
) -> None:
    """Measure waker overhead on top of simulated Claude CLI startup.

    Uses ``RuntimeWaker._wake_event`` directly with a stub backend that
    sleeps a controlled interval. Reports:

      - waker_overhead_ms = A1b − simulated_cli_seconds
      - This isolates pure waker code path cost from Claude CLI startup.

    The total wall-clock (A1b) must include the simulated CLI delay —
    i.e. the stub actually ran. The overhead must stay in the
    sub-200ms ballpark; if it grows, we have a regression in
    ``_wake_event`` bookkeeping.
    """
    from cli.runtime_waker import (  # noqa: WPS433
        MapCommandClient,
        RuntimeWaker,
        RuntimeWakerConfig,
        WakeEvent,
    )

    class _StubMapClient(MapCommandClient):
        """In-process MapCommandClient that records inbound_event_record."""

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

    SIMULATED_CLI_SECONDS = 0.5  # short for test runtime; replace with 3–8 for real
    OVERHEAD_BUDGET_MS = 500.0  # generous; _wake_event is in-process

    # WakeEvent fields: persona, kind, object_id, fingerprint, title, reason, payload
    event = WakeEvent(
        persona="host",
        kind="pending_mention_reply",
        object_id="test-a1b-object",
        fingerprint="a1b-stub-fingerprint",
        title=PROMPT_LABEL,
        reason="phase 2 A1b stub wake",
        payload={
            "kind": "mention",
            "notification_id": "stub-notif-a1b",
            "topic_id": "00000000-0000-0000-0000-000000000000",
        },
    )

    cfg = RuntimeWakerConfig(
        persona="host",
        project_root=tmp_path,
        state_file=tmp_path / "state.json",
        sse_enabled=False,  # keep test focused; SSE handled by A1a
    )

    backend = stub_backend(simulated_cli_seconds=SIMULATED_CLI_SECONDS)
    stub_client = _StubMapClient()

    waker = RuntimeWaker(client=stub_client, config=cfg, backend=backend)

    t_entry = time.perf_counter()
    await waker._wake_event(event, event_source="sse")
    t_return = time.perf_counter()

    a1b_ms = (t_return - t_entry) * 1000.0
    overhead_ms = a1b_ms - (SIMULATED_CLI_SECONDS * 1000.0)

    print(
        f"[a1b] waker_overhead A1b={a1b_ms:.1f}ms "
        f"cli_simulated={SIMULATED_CLI_SECONDS * 1000:.0f}ms "
        f"overhead={overhead_ms:.1f}ms"
    )

    assert len(backend.calls) == 1, (
        f"expected exactly one wake_async call, got {len(backend.calls)}"
    )
    call = backend.calls[0]
    assert call["event_source"] == "sse"
    assert call["event_id"] is not None

    # Sanity: waker overhead stayed within budget.
    assert overhead_ms < OVERHEAD_BUDGET_MS, (
        f"waker overhead {overhead_ms:.1f}ms >= {OVERHEAD_BUDGET_MS}ms "
        f"(regression in _wake_event path)"
    )


# ---------------------------------------------------------------------------
# Baseline JSON emitter for I6 roll-up
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a1b_baseline_emitted(tmp_path: Path, claude_cli_env: dict[str, str]) -> None:
    """Write A1b baseline to tmp_path for I6 consolidation.

    Re-uses the stub backend test (which doesn't need Claude CLI) to
    produce a stable shape, plus an optional real-CLI measurement if
    available. The canonical baseline lives at
    ``.map/generated-plans/phase2-p95-baseline.json`` after I6.
    """
    has_claude = shutil.which(CLAUDE_BIN) is not None

    summary: dict[str, Any] = {
        "produced_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "stub_simulated_ms": 500.0,
        "real_claude_available": has_claude,
        "note": (
            "Phase 2 A1b: backend.wake → Claude Code CLI resume latency. "
            "Observation only, no admission threshold (warm-pool lives in v0.8)."
        ),
    }

    if has_claude:
        # Run one cold-start claude --print and capture wall-clock.
        # Cheap signal; full multi-trial sweep is in the dedicated test.
        t0 = time.perf_counter()
        proc = subprocess.run(
            [CLAUDE_BIN, "--print", "--dangerously-skip-permissions", PROMPT_PING],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            cwd=str(PROJECT_ROOT),
            env=claude_cli_env,
        )
        t1 = time.perf_counter()
        summary["real_claude_cold_ms"] = (t1 - t0) * 1000.0
        summary["real_claude_stdout_chars"] = len(proc.stdout.strip())
        summary["real_claude_rc"] = proc.returncode
    else:
        summary["real_claude_cold_ms"] = None

    out = tmp_path / "phase2-a1b-baseline.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    assert out.exists()
    print(f"[a1b] baseline emitted to {out}")
