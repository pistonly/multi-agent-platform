"""Render per-agent waker liveness at the top of ``map work`` (C2).

Extracted from ``cli/main.py`` so the monolith stays under its line cap
(``test_cli_main_py_under_size_cap``). Pure rendering — the ``stale`` flag
comes from the server (``/status`` ``waker_heartbeats[]``); the CLI never
re-derives it. stale rows emit ``[WARN] waker heartbeat stale for
<agent>(<persona>)``; never (null last_waker_poll_at, no waker poll ever)
shows as a state line WITHOUT a warning, so a "only one waker" deployment keeps
the other personas quiet.

Rendered to stderr so ``map work`` stdout stays a pure YAML document (the
simple-waker parses stdout of its ``map work`` subprocess as YAML). Best
effort: if ``/status`` is unreachable for this agent, the banner is skipped and
``map work`` still succeeds — the work snapshot is authoritative.
"""

from __future__ import annotations

import typer

from map_client.exceptions import MAPHTTPError


def render_waker_heartbeat_banner(client) -> None:
    try:
        status = client.get_global_status()
    except MAPHTTPError:
        return
    rows = getattr(status, "waker_heartbeats", None) or []
    if not rows:
        return
    lines = ["## waker 心跳"]
    for row in rows:
        name = getattr(row, "agent_name", "?")
        persona = getattr(row, "persona", None) or "-"
        stale = bool(getattr(row, "stale", False))
        last = getattr(row, "last_waker_poll_at", None)
        state = "stale" if stale else ("never" if last is None else "ok")
        ts = last.isoformat() if last is not None else "never"
        lines.append(f"{name} ({persona}) last_waker_poll_at={ts} {state}")
        if stale:
            lines.append(f"[WARN] waker heartbeat stale for {name}({persona})")
    if any(bool(getattr(r, "stale", False)) for r in rows):
        lines.append(
            "[HINT] 对应 waker 可能已停机：ps 查 simple-waker；或按 docs/MAP-SIMPLE-WAKER.md "
            "降级路径用 host invoke 编排补位"
        )
    for line in lines:
        typer.echo(line, err=True)
    typer.echo("", err=True)
