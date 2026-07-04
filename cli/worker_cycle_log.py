"""Shared one-line cycle summary logger for host/participant/reviewer workers.

The bridges run as long-lived daemons. Without an explicit per-cycle log
emission, a successful cycle is invisible in stderr logs (only errors are
emitted as JSON events), which makes "is the bridge actually doing work?"
hard to answer from `tail -f .map/bridge-logs/<role>.log`.

This module emits a single JSON line per cycle, shaped like the existing
``_log_event`` events, so users can::

    grep '"action":"cycle_summary"' .map/bridge-logs/host.log

to see exactly what each worker has done over time.
"""

from __future__ import annotations

import json
from typing import Any

import typer


def log_cycle_summary(worker: str, total: Any, *, fields: list[str]) -> None:
    """Emit one JSON line summarising cumulative stats for a worker cycle.

    Args:
        worker: short worker name (``"host"``, ``"participant"``,
            ``"reviewer"``) used as a log filter handle.
        total: stats dataclass exposing the attributes named in ``fields``.
        fields: attribute names on ``total`` to include in the payload.
    """
    payload: dict[str, Any] = {"action": "cycle_summary", "worker": worker}
    for field_name in fields:
        payload[field_name] = getattr(total, field_name, 0)
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True), err=True)
