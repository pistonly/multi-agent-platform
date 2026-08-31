"""Render ``map waker status`` view (实验 waker-status-view I3)。

读 ``.map/simple-waker-state-{persona}.json`` (per-persona 进程状态；
I2 由 waker 自写) + ``map work`` 心跳字段合成视图，输出 10 字段最小集
（persona | pid | uptime | last_poll | busy_since | cycles | reminds |
skips | errors | state）。

视图只读（A4）：不写日志 / 不发通知 / 不重启 waker / 不改 state。
与 T1-T4 单向流一致（视图是末梢，不闭环回去）。

state 派生（实验 T6 a8b64c20 v2 plan）：
  阈值不再硬编码，派生自 ``SimpleWakerConfig.active_interval``
  （``cli/simple_waker.py:201`` in-memory 启动时配置）。
  ``lib/waker_status_config.py`` 单模块导出 4 阈值派生函数：
  - live: ``gap ≤ live_window(active_interval)`` = max(2 × active_interval, 30)
  - stale: live_w < gap ≤ dead_window(active_interval) = 10 × active_interval
  - dead: gap > dead_window 或 pid 不存在
  - busy 卡死升级: ``busy_age > busy_stale(expected_remind_runtime, idle_threshold)``
    （CLI 默认 ``expected_remind_runtime = idle_stale``，故 = 2 × idle_stale）

active_interval 缺失 fallback 默认 30s + ``RuntimeWarning``（case c 覆盖）。
多 waker 配置隔离：每个 waker 独立从 SimpleWakerConfig 派生，不允许全局缓存。
"""
from __future__ import annotations

import json
import os
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib import waker_status_config as _wsc

_busy_stale = _wsc.busy_stale
_dead_window = _wsc.dead_window
_idle_stale = _wsc.idle_stale
_live_window = _wsc.live_window

# active_interval 缺失 fallback 默认值（T6 v2 plan §派生公式 floor 30s 一致）
_ACTIVE_INTERVAL_FALLBACK = 30
_IDLE_INTERVAL_FALLBACK = 300


def _get_active_interval() -> int:
    """Read ``SimpleWakerConfig.active_interval`` (in-memory cli 端配置).

    T5-A I2 未序列化 active_interval 到 state.json；派生源在 cli 启动时配置。
    缺失 fallback 默认 30s + ``RuntimeWarning``（与 §派生公式 floor 一致）。
    """
    try:
        from cli.simple_waker import SimpleWakerConfig  # type: ignore
    except ImportError:
        warnings.warn(
            "cli.simple_waker unavailable; active_interval fallback to 30s",
            RuntimeWarning,
            stacklevel=3,
        )
        return _ACTIVE_INTERVAL_FALLBACK
    value = getattr(SimpleWakerConfig, "active_interval", None)
    if value is None:
        warnings.warn(
            "SimpleWakerConfig.active_interval not found; fallback to 30s",
            RuntimeWarning,
            stacklevel=3,
        )
        return _ACTIVE_INTERVAL_FALLBACK
    return int(value)


def _get_idle_interval() -> int:
    """Read ``SimpleWakerConfig.idle_interval`` (in-memory cli 端配置).

    缺失 fallback 默认 300s + ``RuntimeWarning``。
    """
    try:
        from cli.simple_waker import SimpleWakerConfig  # type: ignore
    except ImportError:
        warnings.warn(
            "cli.simple_waker unavailable; idle_interval fallback to 300s",
            RuntimeWarning,
            stacklevel=3,
        )
        return _IDLE_INTERVAL_FALLBACK
    value = getattr(SimpleWakerConfig, "idle_interval", None)
    if value is None:
        warnings.warn(
            "SimpleWakerConfig.idle_interval not found; fallback to 300s",
            RuntimeWarning,
            stacklevel=3,
        )
        return _IDLE_INTERVAL_FALLBACK
    return int(value)


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        # ISO 8601 with timezone; ``datetime.fromisoformat`` handles +00:00 in Py3.11+
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _pid_alive(pid: int | None) -> bool:
    """Check if pid is owned by current uid and still alive."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    return True


def compute_waker_state(
    persona_state: dict[str, Any],
    *,
    now: datetime | None = None,
    active_interval: int | None = None,
    idle_interval: int | None = None,
) -> str:
    """Compute live / stale / dead / busy_stale for one persona.

    优先级：busy 卡死 > dead > stale > live。
    阈值派生自 ``active_interval`` / ``idle_interval``（cli 端 SimpleWakerConfig
    in-memory 启动时配置；缺失 fallback 默认值 + RuntimeWarning）。
    """
    now = now or datetime.now(timezone.utc)
    pid = persona_state.get("pid")
    if pid is not None:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            pid = None

    last_poll_at = _parse_dt(persona_state.get("last_poll_at"))
    busy_since = _parse_dt(persona_state.get("busy_started_at"))

    # 派生阈值（active_interval/idle_interval 缺失时 fallback + WARN）
    if active_interval is None:
        active_interval = _get_active_interval()
    if idle_interval is None:
        idle_interval = _get_idle_interval()
    live_w = _live_window(active_interval)
    idle_stale_w = _idle_stale(active_interval, idle_interval)
    dead_w = _dead_window(active_interval)
    # busy 卡死升级：CLI 侧 expected_remind_runtime 缺省按 idle_stale 派生
    # busy_stale(expected_remind_runtime=idle_stale_w, idle_threshold=idle_stale_w)
    # = max(idle_stale_w, 2 × idle_stale_w) = 2 × idle_stale_w
    busy_stale_w = _busy_stale(
        expected_remind_runtime=idle_stale_w,
        idle_threshold=idle_stale_w,
    )

    # A2 卡死 busy 升级（防"卡死 busy 逃判"）
    if busy_since is not None and pid is not None and _pid_alive(pid):
        busy_age = (now - busy_since).total_seconds()
        if busy_age > busy_stale_w:
            return "stale"

    # pid 缺失或已死 → dead
    if pid is None or not _pid_alive(pid):
        return "dead"

    # 没记录过 cycle → 视作 dead（首次启动前 / 旧 state 未带 last_poll_at）
    if last_poll_at is None:
        return "dead"

    gap = (now - last_poll_at).total_seconds()
    if gap <= live_w:
        return "live"
    if gap <= dead_w:
        return "stale"
    return "dead"


def _format_uptdelta(start: datetime | None, now: datetime) -> str:
    if start is None:
        return "-"
    delta = now - start
    total = int(delta.total_seconds())
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h{m:02d}m"
    if m > 0:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def collect_waker_status(
    state_dir: Path,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Read all per-persona state files and compute derived fields.

    Returns a list of row dicts (10 fields + 1 derived state). Missing
    state files yield a placeholder row with ``state=never`` and pid=None
    so the operator sees the gap.
    """
    now = now or datetime.now(timezone.utc)
    state_dir = Path(state_dir)
    rows: list[dict[str, Any]] = []

    # 收集所有 persona: host / participant / reviewer 三类默认
    candidates = ("host", "participant", "reviewer")
    seen: set[str] = set()
    for persona in candidates:
        seen.add(persona)
        state_path = state_dir / f"simple-waker-state-{persona}.json"
        persona_state = _load_state_file(state_path)
        rows.append(_row_from_state(persona, persona_state, state_path, now=now))

    # 也扫一遍实际存在的其他 persona state 文件（不假设就 3 个）
    if state_dir.exists():
        for path in sorted(state_dir.glob("simple-waker-state-*.json")):
            persona = path.stem.removeprefix("simple-waker-state-")
            if persona in seen:
                continue
            seen.add(persona)
            persona_state = _load_state_file(path)
            rows.append(_row_from_state(persona, persona_state, path, now=now))

    return rows


def _load_state_file(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    personas = data.get("personas") or {}
    # 每个文件只有一个 persona (named same as filename)
    if not personas:
        return {}
    # 取任意一个 persona（理论上每个文件就一个）
    for pstate in personas.values():
        return pstate if isinstance(pstate, dict) else {}
    return {}


def _row_from_state(
    persona: str,
    persona_state: dict[str, Any],
    state_path: Path,
    *,
    now: datetime,
) -> dict[str, Any]:
    pid = persona_state.get("pid")
    try:
        pid_int = int(pid) if pid is not None else None
    except (TypeError, ValueError):
        pid_int = None

    state = compute_waker_state(persona_state, now=now) if persona_state else "never"

    started_at = _parse_dt(persona_state.get("started_at"))
    last_poll_at = _parse_dt(persona_state.get("last_poll_at"))
    busy_since = _parse_dt(persona_state.get("busy_started_at"))

    return {
        "persona": persona,
        "pid": pid_int,
        "pid_alive": _pid_alive(pid_int) if pid_int is not None else False,
        "uptime": _format_uptdelta(started_at, now),
        "last_poll_at": last_poll_at.isoformat() if last_poll_at else "-",
        "last_poll_gap_s": (
            int((now - last_poll_at).total_seconds()) if last_poll_at else None
        ),
        "busy_since": busy_since.isoformat() if busy_since else "-",
        "cycles_total": int(persona_state.get("cycles_total", 0)),
        "reminds_sent_total": int(persona_state.get("reminds_sent_total", 0)),
        "skips_unchanged_total": int(persona_state.get("skips_unchanged_total", 0)),
        "errors_last_n": int(persona_state.get("errors_last_n", 0)),
        "state": state,
        "_state_file": str(state_path),
    }


def render_waker_status_table(rows: list[dict[str, Any]]) -> str:
    """Render rows as a human-readable markdown table."""
    if not rows:
        return "no waker state files found under .map/"

    # 10 字段（A3 硬上限）+ state
    headers = (
        "persona",
        "pid",
        "uptime",
        "last_poll",
        "busy_since",
        "cycles",
        "reminds",
        "skips",
        "errors",
        "state",
    )

    def _cell(row: dict[str, Any], key: str) -> str:
        if key == "pid":
            pid = row.get("pid")
            return str(pid) if pid is not None else "-"
        if key == "last_poll":
            gap = row.get("last_poll_gap_s")
            if gap is None:
                return row.get("last_poll_at", "-")
            return f"{gap}s ago"
        if key == "uptime":
            return row.get("uptime", "-")
        if key == "busy_since":
            return row.get("busy_since", "-")
        if key == "cycles":
            return str(row.get("cycles_total", 0))
        if key == "reminds":
            return str(row.get("reminds_sent_total", 0))
        if key == "skips":
            return str(row.get("skips_unchanged_total", 0))
        if key == "errors":
            return str(row.get("errors_last_n", 0))
        if key == "state":
            return str(row.get("state", "-"))
        return str(row.get(key, "-"))

    lines: list[str] = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        lines.append("| " + " | ".join(_cell(row, h) for h in headers) + " |")

    # 状态警告行
    warnings: list[str] = []
    for row in rows:
        state = row.get("state")
        persona = row.get("persona", "?")
        if state == "dead":
            warnings.append(
                f"[WARN] {persona} waker state=dead "
                f"(pid={row.get('pid')} last_poll={row.get('last_poll_at')})"
            )
        elif state == "stale":
            warnings.append(
                f"[WARN] {persona} waker state=stale "
                f"(pid={row.get('pid')} last_poll={row.get('last_poll_at')})"
            )
        elif state == "never":
            warnings.append(
                f"[HINT] {persona} waker state=never "
                f"({row.get('_state_file')} missing or empty)"
            )
        if row.get("pid") is not None and not row.get("pid_alive", True):
            warnings.append(
                f"[WARN] {persona} pid={row.get('pid')} not alive "
                f"(进程已死但 state 未清理)"
            )

    if warnings:
        lines.append("")
        lines.extend(warnings)
        lines.append("")
        lines.append(
            "[HINT] 按 docs/MAP-SIMPLE-WAKER.md 重启对应 waker；或 host invoke 编排补位"
        )

    return "\n".join(lines)
