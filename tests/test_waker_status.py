"""Waker status view (实验 waker-status-view I4).

≥5 case 覆盖:
  1. live 渲染: cycle 30s 内 → state=live
  2. stale 渲染: cycle 60s 前 → state=stale
  3. dead 渲染: pid 不存在 → state=dead
  4. 卡死 busy_since=10min 前 → state=stale 而非 live
  5. 重启归档: pid 变化 → cycles_total 归零 + .stale.<ts>.json sidecar 落地

+ 边界 case:
  6. busy_since 缺失 → 走普通 gap 判定（不被 busy 卡死升级误伤）
  7. last_poll_at 缺失 → state=dead（旧 state 未带新字段）
  8. 10 字段硬上限核验（A3）
  9. 视图只读（A4）：collect_waker_status 不产生任何 write IO
 10. _archive_and_reset_on_restart 不影响 runtime session 字段
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def _write_state(state_dir: Path, persona: str, persona_state: dict) -> Path:
    """Write a simple-waker-state file with one persona entry."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"simple-waker-state-{persona}.json"
    data = {"schema_version": 1, "personas": {persona: persona_state}}
    path.write_text(json.dumps(data, ensure_ascii=False))
    return path


# =========================================================================
# Case 1: live 渲染 — cycle 30s 内 → state=live
# =========================================================================


def test_state_live_when_recent_poll(tmp_path: Path) -> None:
    state = {
        "pid": os.getpid(),  # 当前进程一定 alive
        "started_at": _iso(NOW - timedelta(minutes=5)),
        "last_poll_at": _iso(NOW - timedelta(seconds=10)),
        "last_cycle_at": _iso(NOW - timedelta(seconds=10)),
        "cycles_total": 12,
        "reminds_sent_total": 3,
        "skips_unchanged_total": 1,
        "errors_last_n": 0,
    }
    assert compute_waker_state(state, now=NOW) == "live"


# =========================================================================
# Case 2: stale 渲染 — cycle 60s 前 → state=stale
# =========================================================================


def test_state_stale_when_gap_in_window(tmp_path: Path) -> None:
    state = {
        "pid": os.getpid(),
        "started_at": _iso(NOW - timedelta(minutes=10)),
        "last_poll_at": _iso(NOW - timedelta(seconds=60)),
        "cycles_total": 20,
    }
    assert compute_waker_state(state, now=NOW) == "stale"


# =========================================================================
# Case 3: dead 渲染 — pid 不存在 → state=dead
# =========================================================================


def test_state_dead_when_pid_dead(tmp_path: Path) -> None:
    # 用一个明显不存在的 pid（避免与当前进程冲突）
    state = {
        "pid": 2_000_000_000,
        "last_poll_at": _iso(NOW - timedelta(seconds=2)),
        "cycles_total": 5,
    }
    assert compute_waker_state(state, now=NOW) == "dead"


def test_state_dead_when_pid_missing(tmp_path: Path) -> None:
    state = {"last_poll_at": _iso(NOW - timedelta(seconds=2))}
    assert compute_waker_state(state, now=NOW) == "dead"


def test_state_dead_when_last_poll_missing(tmp_path: Path) -> None:
    # 旧 state（I2 之前）：无 pid / 无 last_poll_at
    state = {"claude_session_id": "abc"}
    assert compute_waker_state(state, now=NOW) == "dead"


# =========================================================================
# Case 4: 卡死 busy_since=10min 前 → state=stale 而非 live
# =========================================================================


def test_busy_stuck_upgrades_to_stale(tmp_path: Path) -> None:
    """busy_since 距今 > 5min + pid alive → 升级 stale（防「卡死 busy 逃判」）。"""
    state = {
        "pid": os.getpid(),
        "busy_started_at": _iso(NOW - timedelta(minutes=10)),
        "last_poll_at": _iso(NOW - timedelta(seconds=2)),  # 看似 live
        "cycles_total": 1,
    }
    assert compute_waker_state(state, now=NOW) == "stale"


# =========================================================================
# Case 5: 重启归档 — pid 变化 → cycles_total 归零 + sidecar 落地
# =========================================================================


def test_waker_restart_archives_state_and_resets_counters(tmp_path: Path) -> None:
    """A5: pid 变化 → 写 .stale.<ts>.json + 重置 cycles_total。"""
    from cli.simple_waker import SimpleWaker, SimpleWakerConfig, SimpleWakerStats

    # pre-state: 上次 waker 的 pid=999999 + cycles=42
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

    # 触发累加：当前进程 pid 必然 ≠ 999_999
    stats = SimpleWakerStats(cycles=1)
    waker._accumulate_cycle_stats(stats)

    # 主 state：cycles_total 应归零再 +1 = 1；reminds/skips 归零；errors 归零
    persona_state = waker.state["personas"]["host"]
    assert persona_state["pid"] != 999_999
    assert persona_state["cycles_total"] == 1
    assert persona_state["reminds_sent_total"] == 0
    assert persona_state["skips_unchanged_total"] == 0
    assert persona_state["errors_last_n"] == 0
    # runtime session 字段保留（A5 边界）
    assert persona_state.get("claude_session_id") == "sess-keep-me"

    # sidecar 文件存在
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
                "pid": os.getpid(),  # 同 pid
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
    assert persona_state["cycles_total"] == 8  # 7 + 1
    assert persona_state["reminds_sent_total"] == 2  # stats.reminds_sent=0 不增
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

    # 跑 8 个 cycle：前 5 有错，后 3 干净；window 长度 8，sum = 5
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

    # 跑 15 个全错 cycle；window 被截断到 [1]*10，sum=10
    for _ in range(15):
        stats = SimpleWakerStats(cycles=1, cycle_errors=1)
        waker._accumulate_cycle_stats(stats)

    persona_state = waker.state["personas"]["host"]
    assert len(persona_state["errors_last_n_window"]) == 10
    assert persona_state["errors_last_n"] == 10


# =========================================================================
# Case 6: busy_since 缺失 → 走普通 gap 判定（不被 busy 卡死升级误伤）
# =========================================================================


def test_state_live_without_busy_when_recent(tmp_path: Path) -> None:
    state = {
        "pid": os.getpid(),
        "last_poll_at": _iso(NOW - timedelta(seconds=2)),
        # busy_since 缺失
        "cycles_total": 5,
    }
    assert compute_waker_state(state, now=NOW) == "live"


# =========================================================================
# Case 7: 10 字段硬上限核验（A3）
# =========================================================================


def test_render_table_has_exactly_10_columns() -> None:
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


# =========================================================================
# Case 8: 视图只读（A4）— collect_waker_status 不产生任何 write IO
# =========================================================================


def test_collect_waker_status_is_read_only(tmp_path: Path) -> None:
    state = {
        "pid": os.getpid(),
        "started_at": _iso(NOW - timedelta(minutes=1)),
        "last_poll_at": _iso(NOW - timedelta(seconds=5)),
        "cycles_total": 1,
    }
    _write_state(tmp_path, "host", state)

    initial_mtime = tmp_path.stat().st_mtime
    initial_files = set(tmp_path.iterdir())

    rows = collect_waker_status(tmp_path, now=NOW)

    # 文件集合不变
    assert set(tmp_path.iterdir()) == initial_files
    # mtime 不变
    assert tmp_path.stat().st_mtime == initial_mtime
    # 至少返回一行
    assert len(rows) >= 1
    assert rows[0]["persona"] == "host"


# =========================================================================
# Case 9: render 输出含死/旧警告
# =========================================================================


def test_render_emits_warn_for_dead_state(tmp_path: Path) -> None:
    rows = collect_waker_status(tmp_path, now=NOW)  # 空目录 → 全 never
    out = render_waker_status_table(rows)
    assert "[HINT]" in out or "[WARN]" in out


# =========================================================================
# Case 10: collect_waker_status 扫描所有 persona state 文件
# =========================================================================


def test_collect_finds_additional_persona_files(tmp_path: Path) -> None:
    """除默认 host/participant/reviewer 外，额外的 persona state 文件也应被扫到。"""
    state = {
        "pid": os.getpid(),
        "started_at": _iso(NOW - timedelta(minutes=1)),
        "last_poll_at": _iso(NOW - timedelta(seconds=2)),
        "cycles_total": 1,
    }
    _write_state(tmp_path, "custom-persona", state)

    rows = collect_waker_status(tmp_path, now=NOW)
    personas = {r["persona"] for r in rows}
    assert "custom-persona" in personas
    assert "host" in personas  # 默认仍包含
