"""F4 (v0.13 M59a): scan_plane perf baseline.

`scan_plane` is a per-request full parse of the ``map/`` content plane (no
cache, by design — see the trigger-condition note on the function itself).
This test records the current cost as a stable JSON artifact under
``tests/perf-baselines/`` (same mechanism as the waker phase1 baseline in
``test_waker_phase1_acceptance.py``) so any future optimization work has a
known before/after comparison point.

Run it with::

    pytest tests/test_fs_scan_plane_perf_baseline.py -m slow

The assertion threshold (1.0 s p95) is a generous regression tripwire, NOT
the optimization trigger — the real trigger condition lives in the
``scan_plane`` docstring (topics > 500 or p95 > 100 ms → re-measure and
consider mtime-incremental scanning).
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

import pytest
from map_fs.topic_parser import scan_plane

pytestmark = pytest.mark.slow

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ITERATIONS = 50
_P95_TRIPWIRE_SECONDS = 1.0


def _percentile(samples: list[float], pct: float) -> float:
    """Nearest-rank percentile (same shape as the phase1 baseline helper)."""
    ordered = sorted(samples)
    idx = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[idx]


def test_scan_plane_perf_baseline() -> None:
    samples: list[float] = []
    topic_count = experiment_count = 0
    for _ in range(_ITERATIONS):
        start = datetime.now(timezone.utc).timestamp()
        plane = scan_plane(_REPO_ROOT)
        elapsed = datetime.now(timezone.utc).timestamp() - start
        samples.append(elapsed)
        topic_count = len(plane.topics)
        experiment_count = len(plane.experiments)

    p50 = _percentile(samples, 50)
    p95 = _percentile(samples, 95)
    p99 = _percentile(samples, 99)
    print(
        f"[M59a baseline] topics={topic_count} experiments={experiment_count} "
        f"n={len(samples)} p50={p50:.6f}s p95={p95:.6f}s p99={p99:.6f}s "
        f"mean={statistics.fmean(samples):.6f}s"
    )

    assert p95 < _P95_TRIPWIRE_SECONDS, (
        f"scan_plane p95 {p95:.3f}s exceeded the {_P95_TRIPWIRE_SECONDS}s "
        "tripwire — re-run to confirm, then triage before it lands"
    )

    baseline_path = (
        Path(__file__).resolve().parent / "perf-baselines" / "fs-scan-plane-baseline.json"
    )
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(
        json.dumps(
            {
                "phase": "fs-scan-plane",
                "iterations": _ITERATIONS,
                "counts": {"topics": topic_count, "experiments": experiment_count},
                "p50_seconds": p50,
                "p95_seconds": p95,
                "p99_seconds": p99,
                "mean_seconds": statistics.fmean(samples),
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "note": (
                    "full-repo scan_plane (per-request full parse, no cache by "
                    "design). Optimization trigger (see scan_plane docstring): "
                    "topics > 500 or p95 > 100ms -> re-measure this baseline "
                    "before introducing mtime-incremental scanning. Counts "
                    "snapshot travels with the file so future diffs can tell "
                    "scale-driven drift from code-driven drift."
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    assert baseline_path.exists()
