"""Render per-agent waker liveness at the top of ``map work`` (C2).

Extracted from ``cli/main.py`` so the monolith stays under its line cap
(``test_cli_main_py_under_size_cap``). Pure rendering — the ``stale`` flag
comes from the server (``/status`` ``waker_heartbeats[]``); the CLI never
re-derives it. stale rows emit ``[WARN] waker heartbeat stale for
<agent>(<persona>)``; never (null last_waker_poll_at, no waker poll ever)
shows as a state line WITHOUT a warning, so a "only one waker" deployment keeps
the other personas quiet.

实验 b3ec2e4d I4：新增 ``last_busy_since`` 字段渲染——无值不显示；
busy 行即使 stale 也不输出 stale WARN（busy 期间 polling 暂停属正常）；
busy > 2h 输出软警告 ``[WARN] busy > 2h``（覆盖 idle stale 之外的真
「卡死但 polling 心跳仍新」场景）。

Rendered to stderr so ``map work`` stdout stays a pure YAML document (the
simple-waker parses stdout of its ``map work`` subprocess as YAML). Best
effort: if ``/status`` is unreachable for this agent, the banner is skipped and
``map work`` still succeeds — the work snapshot is authoritative.
"""

from __future__ import annotations

from datetime import datetime, timezone

import typer
from map_client.exceptions import MAPHTTPError

# 软警告阈值（与 server 端 busy_tolerance 不同——server 30min 判活，
# 这里 2h 是「这么久还 busy 一定有事故」的运维阈值，A5）。
_BUSY_SOFT_WARN_HOURS = 2


def render_waker_heartbeat_banner(client) -> None:
    try:
        status = client.get_global_status()
    except MAPHTTPError:
        return
    rows = getattr(status, "waker_heartbeats", None) or []
    if not rows:
        return
    lines = ["## waker 心跳"]
    now = datetime.now(timezone.utc)
    any_stale = False
    any_busy_warn = False
    for row in rows:
        name = getattr(row, "agent_name", "?")
        persona = getattr(row, "persona", None) or "-"
        stale = bool(getattr(row, "stale", False))
        last = getattr(row, "last_waker_poll_at", None)
        busy_since = getattr(row, "last_busy_since", None)
        is_busy = busy_since is not None
        # busy 期间 polling cycle 暂停属正常，stale WARN 不输出。
        # 但 busy > 2h 是软警告（覆盖「卡死但 heartbeat 仍新」的盲区）。
        state = "busy" if is_busy else ("stale" if stale else ("never" if last is None else "ok"))
        ts = last.isoformat() if last is not None else "never"
        busy_str = ""
        if is_busy and busy_since is not None:
            busy_str = f" last_busy_since={busy_since.isoformat()}"
        lines.append(f"{name} ({persona}) last_waker_poll_at={ts}{busy_str} {state}")
        if stale and not is_busy:
            lines.append(f"[WARN] waker heartbeat stale for {name}({persona})")
            any_stale = True
        if is_busy and busy_since is not None:
            age = now - busy_since
            if age.total_seconds() > _BUSY_SOFT_WARN_HOURS * 3600:
                lines.append(
                    f"[WARN] busy > {_BUSY_SOFT_WARN_HOURS}h for {name}({persona}) "
                    f"({age})"
                )
                any_busy_warn = True
    if any_stale:
        lines.append(
            "[HINT] 对应 waker 可能已停机：ps 查 simple-waker；或按 docs/MAP-SIMPLE-WAKER.md "
            "降级路径用 host invoke 编排补位"
        )
    if any_busy_warn:
        lines.append(
            "[HINT] busy 超过 2h 视为真卡死：ps 查 waker / runtime 子进程；"
            "或重启对应 waker（_check_busy_crash_recovery 会清 server 列）"
        )
    for line in lines:
        typer.echo(line, err=True)
    typer.echo("", err=True)
