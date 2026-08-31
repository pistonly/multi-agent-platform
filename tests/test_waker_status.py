"""Waker status view (实验 waker-status-view I4 + 实验 T6 a8b64c20 I4).

档位断言 parametrize（T6 §派生公式 设计：阈值派生自 active_interval / idle_interval，
live_window = max(2×active_interval, 30)；idle_stale = max(3×active_interval, idle_interval)；
dead_window = 10×active_interval；busy_stale = max(expected_remind_runtime, 2×idle_threshold)）。

Case 1-7: 档位表 parametrize（live/stale/dead/busy_stale）
Case 8-9: dead pid 边界
Case 10-11: busy 卡死升级档位
Case 12-13: floor 30s 边界（active_interval 极短场景）
Case 14-16: 视图只读 + 渲染 + 字段上限（A3/A4）
Case 17-18: 多 persona + 10 列硬上限
Case 19-21: 重启归档 + errors 滚动窗口
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

waker_view = importlib.import_module("cli.waker_status_view")
collect_waker_status = waker_view.collect_waker_status
compute_waker_state = waker_view.compute_waker_state
render_waker_status_table = waker_view.render_waker_status_table

# =========================================================================
# Helpers
# =========================================================================


def _iso(dt: datetime) -> str:
    return dt.isoformat()


NOW = datetime(2026, 8, 31, 1, 0, 0, tzinfo=timezone.utc)
DEFAULT_ACTIVE_INTERVAL = 30
DEFAULT_IDLE_INTERVAL = 300


def _write_state(state_dir: Path, persona: str, persona_state: dict) -> Path:
    """Write a simple-waker-state file with one persona entry."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"simple-waker-state-{persona}.json"
    data = {"schema_version": 1, "personas": {persona: persona_state}}
    path.write_text(json.dumps(data, ensure_ascii=False))
    return path


def _state(
    pid: int | None = os.getpid(),
    last_poll_gap_s: float | None = 2.0,
    busy_age_s: float | None = None,
    cycles_total: int = 0,
    started_at_gap_min: float | None = 5.0,
) -> dict:
    """Build a state dict with parametrized gap / busy_age values."""
    state: dict = {"cycles_total": cycles_total}
    if pid is not None:
        state["pid"] = pid
    if started_at_gap_min is not None:
        state["started_at"] = _iso(NOW - timedelta(minutes=started_at_gap_min))
    if last_poll_gap_s is not None:
        state["last_poll_at"] = _iso(NOW - timedelta(seconds=last_poll_gap_s))
    if busy_age_s is not None:
        state["busy_started_at"] = _iso(NOW - timedelta(seconds=busy_age_s))
    return state


# =========================================================================
# Case 1-5: 档位表 parametrize（active_interval=30, idle_interval=300 默认）
# 派生阈值：
#   live_w = max(2×30, 30) = 60
#   idle_stale_w = max(3×30, 300) = 300
#   dead_w = 10×30 = 300
#   busy_stale_w = max(300, 2×300) = 600
# =========================================================================


@pytest.mark.parametrize(
    "gap,expected",
    [
        # live 档位（gap ≤ 60）
        (0.0, "live"),
        (30.0, "live"),
        (60.0, "live"),  # 边界 60 ≤ 60
        # stale 档位（60 < gap ≤ 300）
        (61.0, "stale"),
        (150.0, "stale"),
        (300.0, "stale"),  # 边界 300 ≤ 300
        # dead 档位（gap > 300）
        (301.0, "dead"),
        (600.0, "dead"),
        (3600.0, "dead"),
    ],
)
def test_state_tier_gap_only(tmp_path: Path, gap: float, expected: str) -> None:
    """§派生公式 档位表：默认 active_interval=30 下 gap → state 派生。

    boundary 包含：gap=60 → live（≤60），gap=300 → stale（≤300 dead_w），
    gap=301 → dead（>300）。
    """
    state = _state(last_poll_gap_s=gap)
    assert compute_waker_state(state, now=NOW, active_interval=DEFAULT_ACTIVE_INTERVAL, idle_interval=DEFAULT_IDLE_INTERVAL) == expected


# =========================================================================
# Case 6-7: floor 30s 边界（active_interval=5, 极短场景）
# 派生阈值：
#   live_w = max(2×5, 30) = 30（floor 30s 生效）
#   idle_stale_w = max(3×5, 300) = 300
#   dead_w = 10×5 = 50
# =========================================================================


@pytest.mark.parametrize(
    "gap,expected",
    [
        (0.0, "live"),
        (29.0, "live"),  # floor 30 之内
        (30.0, "live"),  # 边界
        (31.0, "stale"),  # > 30 但 ≤ 50
        (50.0, "stale"),  # 边界 = dead_w
        (51.0, "dead"),  # > 50
    ],
)
def test_state_tier_floor_30s_with_short_active_interval(tmp_path: Path, gap: float, expected: str) -> None:
    """§派生公式 floor 30s：active_interval=5 时 live_w 仍 = 30（不 = 10）。"""
    state = _state(last_poll_gap_s=gap)
    assert compute_waker_state(state, now=NOW, active_interval=5, idle_interval=DEFAULT_IDLE_INTERVAL) == expected


# =========================================================================
# Case 8-9: dead pid 边界（pid 不存在 / pid 缺失）
# =========================================================================


def test_state_dead_when_pid_dead(tmp_path: Path) -> None:
    """pid 不存在 → dead（即便 last_poll_at 看起来新）。"""
    state = _state(pid=2_000_000_000, last_poll_gap_s=2.0)
    assert compute_waker_state(state, now=NOW) == "dead"


def test_state_dead_when_last_poll_missing(tmp_path: Path) -> None:
    """旧 state：last_poll_at 缺失 → dead（首次启动前 / 旧 schema 未带）。"""
    state = {"claude_session_id": "abc"}
    assert compute_waker_state(state, now=NOW) == "dead"


# =========================================================================
# Case 10-11: busy 卡死升级档位 parametrize（active_interval=30, idle_interval=300）
# busy_stale_w = max(300, 2×300) = 600
#   busy_age ≤ 600 → 不升级；busy_age > 600 → 升级 stale
# =========================================================================


@pytest.mark.parametrize(
    "busy_age_s,last_poll_gap_s,expected",
    [
        # busy_age 未超 busy_stale（600）→ 走普通 gap 判定
        (300.0, 2.0, "live"),  # busy 5min + gap 2s → live（busy_age ≤ 600）
        (600.0, 2.0, "live"),  # busy 10min + gap 2s → live（busy_age = 600, NOT > 600）
        # busy_age 超 busy_stale → 升级 stale（即便 last_poll_at 看似 live）
        (601.0, 2.0, "stale"),  # busy 10min+1s + gap 2s → stale（busy 卡死升级）
        (1200.0, 2.0, "stale"),  # busy 20min → stale
        # busy 但 gap 也 stale 时仍 stale
        (700.0, 150.0, "stale"),  # busy_age > 600 → stale
    ],
)
def test_busy_stuck_tier(tmp_path: Path, busy_age_s: float, last_poll_gap_s: float, expected: str) -> None:
    """§派生公式 busy_stale：busy_age > 600 → 升级 stale（防「卡死 busy 逃判」）。"""
    state = _state(busy_age_s=busy_age_s, last_poll_gap_s=last_poll_gap_s)
    assert compute_waker_state(state, now=NOW, active_interval=DEFAULT_ACTIVE_INTERVAL, idle_interval=DEFAULT_IDLE_INTERVAL) == expected


# =========================================================================
# Case 12: busy_since 缺失 → 走普通 gap 判定
# =========================================================================


def test_state_live_without_busy_when_recent(tmp_path: Path) -> None:
    """busy_since 缺失时不被 busy 卡死升级误伤。"""
    state = _state(busy_age_s=None, last_poll_gap_s=2.0)
    assert compute_waker_state(state, now=NOW) == "live"


# =========================================================================
# Case 13-21: 视图层 / 渲染层 / 重启归档 / errors 滚动窗口
# =========================================================================


def test_render_table_has_exactly_10_columns() -> None:
    """A3：10 字段硬上限。"""
    rows = [
        {
            "persona": "host",
            "pid": 1,
            "pid_alive": True,
            "uptime": "1m",
            "last_poll_at": "x",
            "last_poll_gap_s": 1,
            "busy_since": "-",
            "cycles_total": 1,
            "reminds_sent_total": 0,
            "skips_unchanged_total": 0,
            "errors_last_n": 0,
            "state": "live",
            "_state_file": "x",
        }
    ]
    out = render_waker_status_table(rows)
    header_line = next(line for line in out.splitlines() if line.startswith("| persona"))
    cols = [c.strip() for c in header_line.strip("|").split("|")]
    assert len(cols) == 10
    assert cols == [
        "persona", "pid", "uptime", "last_poll", "busy_since",
        "cycles", "reminds", "skips", "errors", "state",
    ]


def test_collect_waker_status_is_read_only(tmp_path: Path) -> None:
    """A4：collect_waker_status 不产生任何 write IO。"""
    state = _state(last_poll_gap_s=5.0, started_at_gap_min=1.0, cycles_total=1)
    _write_state(tmp_path, "host", state)

    initial_mtime = tmp_path.stat().st_mtime
    initial_files = set(tmp_path.iterdir())

    rows = collect_waker_status(tmp_path, now=NOW)

    assert set(tmp_path.iterdir()) == initial_files
    assert tmp_path.stat().st_mtime == initial_mtime
    assert len(rows) >= 1
    assert rows[0]["persona"] == "host"


def test_render_emits_warn_for_dead_state(tmp_path: Path) -> None:
    """空目录 → 全 never；render 含 [HINT]/[WARN]。"""
    rows = collect_waker_status(tmp_path, now=NOW)
    out = render_waker_status_table(rows)
    assert "[HINT]" in out or "[WARN]" in out


def test_collect_finds_additional_persona_files(tmp_path: Path) -> None:
    """除默认 host/participant/reviewer 外，额外的 persona state 文件也应被扫到。"""
    state = _state(last_poll_gap_s=2.0, started_at_gap_min=1.0, cycles_total=1)
    _write_state(tmp_path, "custom-persona", state)

    rows = collect_waker_status(tmp_path, now=NOW)
    personas = {r["persona"] for r in rows}
    assert "custom-persona" in personas
    assert "host" in personas


def test_waker_restart_archives_state_and_resets_counters(tmp_path: Path) -> None:
    """A5：pid 变化 → 写 .stale.<ts>.json + 重置 cycles_total。"""
    from cli.simple_waker import SimpleWaker, SimpleWakerConfig, SimpleWakerStats

    state_file = tmp_path / "simple-waker-state-host.json"
    pre = {
        "schema_version": 1,
        "personas": {
            "host": {
                "pid": 999_999,
                "started_at": _iso(NOW - timedelta(hours=2)),
                "cycles_total": 42,
                "reminds_sent_total": 10,
                "skips_unchanged_total": 5,
                "errors_last_n": 2,
                "claude_session_id": "sess-keep-me",
                "last_poll_at": _iso(NOW - timedelta(seconds=10)),
            }
        },
    }
    state_file.write_text(json.dumps(pre, ensure_ascii=False))

    config = SimpleWakerConfig(
        persona="host",
        state_file=state_file,
        map_cmd="map",
    )
    waker = SimpleWaker.__new__(SimpleWaker)
    waker.config = config
    waker._state_dirty = False
    waker.state = json.loads(state_file.read_text())

    stats = SimpleWakerStats(cycles=1)
    waker._accumulate_cycle_stats(stats)

    persona_state = waker.state["personas"]["host"]
    assert persona_state["pid"] != 999_999
    assert persona_state["cycles_total"] == 1
    assert persona_state["reminds_sent_total"] == 0
    assert persona_state["skips_unchanged_total"] == 0
    assert persona_state["errors_last_n"] == 0
    assert persona_state.get("claude_session_id") == "sess-keep-me"

    sidecars = list(tmp_path.glob("simple-waker-state-host.stale.*.json"))
    assert len(sidecars) == 1
    sidecar_data = json.loads(sidecars[0].read_text())
    assert sidecar_data["previous_pid"] == 999_999
    assert sidecar_data["previous_cycles_total"] == 42
    assert sidecar_data["previous_reminds_sent_total"] == 10
    assert sidecar_data["previous_errors_last_n"] == 2


def test_waker_no_archive_when_pid_unchanged(tmp_path: Path) -> None:
    """pid 不变 → 不写 sidecar、不重置 counters。"""
    from cli.simple_waker import SimpleWaker, SimpleWakerConfig, SimpleWakerStats

    state_file = tmp_path / "simple-waker-state-host.json"
    pre = {
        "schema_version": 1,
        "personas": {
            "host": {
                "pid": os.getpid(),
                "started_at": _iso(NOW - timedelta(hours=1)),
                "cycles_total": 7,
                "reminds_sent_total": 2,
                "last_poll_at": _iso(NOW - timedelta(seconds=2)),
            }
        },
    }
    state_file.write_text(json.dumps(pre, ensure_ascii=False))

    config = SimpleWakerConfig(persona="host", state_file=state_file, map_cmd="map")
    waker = SimpleWaker.__new__(SimpleWaker)
    waker.config = config
    waker._state_dirty = False
    waker.state = json.loads(state_file.read_text())

    stats = SimpleWakerStats(cycles=1)
    waker._accumulate_cycle_stats(stats)

    persona_state = waker.state["personas"]["host"]
    assert persona_state["cycles_total"] == 8
    assert persona_state["reminds_sent_total"] == 2
    assert list(tmp_path.glob("simple-waker-state-host.stale.*.json")) == []


def test_waker_accumulates_errors_in_rolling_window(tmp_path: Path) -> None:
    """errors_last_n 是 10 cycle 滚动窗口。"""
    from cli.simple_waker import SimpleWaker, SimpleWakerConfig, SimpleWakerStats

    state_file = tmp_path / "simple-waker-state-host.json"
    state_file.write_text(json.dumps({"schema_version": 1, "personas": {"host": {}}}))

    config = SimpleWakerConfig(persona="host", state_file=state_file, map_cmd="map")
    waker = SimpleWaker.__new__(SimpleWaker)
    waker.config = config
    waker._state_dirty = False
    waker.state = json.loads(state_file.read_text())

    for i in range(8):
        stats = SimpleWakerStats(cycles=1, cycle_errors=1 if i < 5 else 0)
        waker._accumulate_cycle_stats(stats)

    persona_state = waker.state["personas"]["host"]
    assert persona_state["errors_last_n"] == 5
    assert len(persona_state["errors_last_n_window"]) == 8
    assert sum(persona_state["errors_last_n_window"]) == 5


def test_waker_errors_window_caps_at_10(tmp_path: Path) -> None:
    """errors_last_n_window 上限 10。"""
    from cli.simple_waker import SimpleWaker, SimpleWakerConfig, SimpleWakerStats

    state_file = tmp_path / "simple-waker-state-host.json"
    state_file.write_text(json.dumps({"schema_version": 1, "personas": {"host": {}}}))

    config = SimpleWakerConfig(persona="host", state_file=state_file, map_cmd="map")
    waker = SimpleWaker.__new__(SimpleWaker)
    waker.config = config
    waker._state_dirty = False
    waker.state = json.loads(state_file.read_text())

    for _ in range(15):
        stats = SimpleWakerStats(cycles=1, cycle_errors=1)
        waker._accumulate_cycle_stats(stats)

    persona_state = waker.state["personas"]["host"]
    assert len(persona_state["errors_last_n_window"]) == 10
    assert persona_state["errors_last_n"] == 10
