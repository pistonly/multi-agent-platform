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


# =========================================================================
# I5 新增 5 case (a)-(e) — 验证 §派生公式 完整性
# =========================================================================


@pytest.mark.parametrize(
    "active_interval,idle_interval,multiplier,expected",
    [
        # active_interval=30, idle_interval=300 (默认): live_w=60, idle_stale_w=300, dead_w=300
        (30, 300, 0.5, "live"),    # 15s → live
        (30, 300, 1.5, "live"),    # 45s → live
        (30, 300, 2.5, "stale"),   # 75s → stale (plan §A5(a) 期望 live，但 live_w=60 < 75 < dead_w=300 → stale)
        (30, 300, 3.5, "stale"),   # 105s → stale
        (30, 300, 10.0, "stale"),  # 300s → stale (boundary = dead_w)
        (30, 300, 10.5, "dead"),   # 315s → dead
        # active_interval=60, idle_interval=300: live_w=120, idle_stale_w=300, dead_w=600
        (60, 300, 1.0, "live"),    # 60s → live
        (60, 300, 2.0, "live"),    # 120s → live (boundary)
        (60, 300, 3.0, "stale"),   # 180s → stale
        (60, 300, 10.0, "stale"),  # 600s → stale (boundary)
        (60, 300, 10.5, "dead"),   # 630s → dead
    ],
)
def test_a_derived_tier_table(
    tmp_path: Path, active_interval: int, idle_interval: int, multiplier: float, expected: str
) -> None:
    """§A5(a) 派生档位表：gap = multiplier × active_interval → 派生 state。

    Plan §A5(a) 期望 `live/live/live/stale/dead`，但 live_w = max(2×active_interval, 30)
    公式下 0.5×/1.5× → live、2.5× → stale（> live_w=60 但 ≤ dead_w=300）、
    3.5× → stale、10× → stale (boundary = dead_w)。差异源于 §派生公式 2× 不是 3×
    设计选择（plan §风险 1.5× 不选 / 3× 不选 论证）。本 case 覆盖完整档位表
    live/stale/dead 三档 + 边界，断言与公式严格对齐。
    """
    gap = multiplier * active_interval
    state = _state(last_poll_gap_s=gap)
    assert (
        compute_waker_state(
            state, now=NOW, active_interval=active_interval, idle_interval=idle_interval
        )
        == expected
    )


@pytest.mark.parametrize(
    "active_interval,idle_interval,busy_multiplier,expected",
    [
        # active_interval=30, idle_interval=300: busy_stale_w = max(300, 600) = 600
        (30, 300, 0.8, "live"),    # busy 480s → live (busy_age ≤ 600)
        (30, 300, 1.0, "live"),    # busy 600s → live (busy_age = busy_stale_w, NOT > 600)
        (30, 300, 1.5, "stale"),   # busy 900s → stale (busy_age > 600)
        (30, 300, 3.0, "stale"),   # busy 1800s → stale
        # active_interval=60, idle_interval=300: busy_stale_w = max(300, 600) = 600
        (60, 300, 0.8, "live"),    # busy 480s → live
        (60, 300, 1.5, "stale"),   # busy 900s → stale
    ],
)
def test_b_busy_stuck_tier_table(
    tmp_path: Path, active_interval: int, idle_interval: int, busy_multiplier: float, expected: str
) -> None:
    """§A5(b) busy 升级档位：busy_age = multiplier × busy_stale_w → 派生 state。

    Plan §A5(b) 期望 `live/live/busy_stale`，实际 busy_stale_w 严格/非严格边界：
    busy_age ≤ busy_stale_w → 走普通 gap 判定；busy_age > busy_stale_w → stale 升级。
    multiplier 0.8/1.0 → busy_age ≤ 600 → live（视 gap）；1.5/3.0 → busy_age > 600 → stale。
    """
    # busy_stale_w via lib formula: max(idle_stale, 2 × idle_stale) = 2 × idle_stale for idle_stale=300
    busy_stale_w = 2 * idle_interval  # = 600 for idle=300
    busy_age = busy_multiplier * busy_stale_w
    state = _state(busy_age_s=busy_age, last_poll_gap_s=2.0)
    assert (
        compute_waker_state(
            state, now=NOW, active_interval=active_interval, idle_interval=idle_interval
        )
        == expected
    )


def test_c_active_interval_missing_falls_back_to_30s() -> None:
    """§A5(c) active_interval 缺失降级：fallback 默认 30s + RuntimeWarning。

    compute_waker_state 不传 active_interval 时从 SimpleWakerConfig 派生；
    SimpleWakerConfig import 失败 / 属性缺失 → fallback 30s + warn。
    本 case mock SimpleWakerConfig.active_interval = None 触发 fallback，
    验证 gap=60s（边界 = 2×30）→ live（不是 stale）。
    """
    import warnings as _warnings
    from unittest.mock import patch

    # SimpleWakerConfig.active_interval = None → fallback 30s
    with patch("cli.simple_waker.SimpleWakerConfig") as mock_config:
        mock_config.active_interval = None
        state = _state(last_poll_gap_s=60)  # boundary 60 = 2×30
        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            result = compute_waker_state(state, now=NOW)
        # fallback 30s → live_w = 60 → gap=60 ≤ 60 → live
        assert result == "live"
        # 至少一条 RuntimeWarning
        assert any(issubclass(w.category, RuntimeWarning) for w in caught)


def test_d_multi_waker_per_call_kwargs_override() -> None:
    """§A5(d) 多 waker 不同 active_interval 各自派生不全局缓存。

    compute_waker_state 接受 per-call active_interval / idle_interval kwargs，
    实现「同一 CLI 进程派生不同 persona 不同配置」隔离。
    """
    state_a = _state(last_poll_gap_s=60)  # 60s gap
    state_b = _state(last_poll_gap_s=60)
    # active_interval=30 → live_w=60 → gap=60 → live
    a = compute_waker_state(state_a, now=NOW, active_interval=30, idle_interval=300)
    # active_interval=10 → live_w=max(20, 30)=30 → gap=60 > 30 → stale
    b = compute_waker_state(state_b, now=NOW, active_interval=10, idle_interval=300)
    assert a == "live", f"expected live for active=30 gap=60, got {a}"
    assert b == "stale", f"expected stale for active=10 gap=60, got {b}"
    # 同一进程两次调用互不污染
    assert (
        compute_waker_state(state_a, now=NOW, active_interval=30, idle_interval=300) == "live"
    )


def test_e_legacy_state_file_compatibility() -> None:
    """§A5(e) 跨版本兼容：T5-A I2 之前 state.json 缺 active_interval → 不抛错。

    旧 state schema 只含 pid/started_at/last_poll_at/cycles_total，
    无 busy_started_at / active_interval / idle_interval。compute_waker_state
    应容忍缺失字段：busy_since=None → 走普通 gap 判定；active_interval=None →
    fallback 30s。
    """
    import warnings as _warnings
    from unittest.mock import patch

    # 模拟 SimpleWakerConfig 缺失（fallback 路径）
    with patch("cli.simple_waker.SimpleWakerConfig") as mock_config:
        mock_config.active_interval = None
        mock_config.idle_interval = None
        legacy_state = {
            "pid": os.getpid(),
            "started_at": _iso(NOW - timedelta(minutes=5)),
            "last_poll_at": _iso(NOW - timedelta(seconds=2)),
            "cycles_total": 1,
            # 无 busy_started_at / active_interval / idle_interval
        }
        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            result = compute_waker_state(legacy_state, now=NOW)
        # fallback 30s → live_w=60 → gap=2 ≤ 60 → live
        assert result == "live"
        # 至少一条 RuntimeWarning（active_interval 或 idle_interval 缺失）
        assert any(issubclass(w.category, RuntimeWarning) for w in caught)


# =========================================================================
# I6 回归保护 fixture — 复现 host 描述的 T5-A 上线即误报场景
# =========================================================================


@pytest.mark.parametrize(
    "gap_s,expected",
    [
        # T5-A 715202a3 上线即误报复现：gap=51 active_interval=30
        # 旧 LIVE_WINDOW_SECONDS=30 → 51 > 30 误判 stale
        # 新派生 live_w=max(60, 30)=60 → 51 ≤ 60 → live（修复）
        (51, "live"),  # 主复现 fixture（plan §A6）
        # 边界附近：T5-A 实际 poll 间隙常态 26-51s（含网络抖动）
        (26, "live"),  # 常态下限
        (40, "live"),  # 常态中位
        (61, "stale"),  # 边界外 1s → 旧公式仍 stale，新公式 stale（live_w=60 < 61）
        # active_interval=30, idle_interval=300 默认配置下不应误报任何 ≤ 60 gap
        (1, "live"),
        (30, "live"),
        (60, "live"),  # boundary
    ],
)
def test_regression_t5a_poll_gap_no_false_stale(
    tmp_path: Path, gap_s: float, expected: str
) -> None:
    """§A6 回归保护 fixture：active_interval=30, gap=51 → live（修复 T5-A 上线即误报）。

    T5-A (715202a3) 闭环事故：waker active_interval=30 正常轮询 + poll 间隙常态
    26-51s（含网络抖动），同时刻 `map work` 全 ok，但 `map waker status` 标 stale。
    根因：`cli/waker_status_view.py:26 LIVE_WINDOW_SECONDS = 30` 硬编码 ——
    阈值与轮询周期同量级导致高误报。

    本 fixture 复现事故场景，验证：
    1. gap=51（事故态）→ live（修复有效）
    2. gap 26/40/60（常态抖动 + 边界）→ live（无新误报）
    3. gap=61（边界外 1s）→ stale（避免过度放宽阈值，确保告警仍生效）
    """
    state = _state(last_poll_gap_s=gap_s)
    assert (
        compute_waker_state(
            state, now=NOW, active_interval=DEFAULT_ACTIVE_INTERVAL, idle_interval=DEFAULT_IDLE_INTERVAL
        )
        == expected
    )


def test_regression_t5a_collaborative_with_map_work(tmp_path: Path) -> None:
    """§A7 同帧一致性 fixture（plan §风险 修复 T5-A 闭环遗漏）：`map waker status` 与 `map work` 同帧判定对齐。

    模拟场景：3 waker (host/participant/reviewer) 均在 active_interval=30 + idle_interval=300 下
    正常轮询，poll 间隙 26-51s 常态。同时刻调用 `compute_waker_state`（cli 视图）应输出 live；
    `map work` 同源数据由 server `status_service.build_waker_heartbeats` 派生，对应 `last_waker_poll_at`
    距今 ≤ 60s → ok（非 stale）。本 fixture 模拟两侧同源判定，避免 T5-A 同帧不一致事故复现。
    """
    # 3 persona 同时刻 poll gap = 26/40/51（T5-A 常态抖动范围）
    personas = {
        "host": 26.0,
        "participant": 40.0,
        "reviewer": 51.0,
    }
    for persona, gap in personas.items():
        state = _state(last_poll_gap_s=gap)
        cli_state = compute_waker_state(
            state, now=NOW, active_interval=DEFAULT_ACTIVE_INTERVAL, idle_interval=DEFAULT_IDLE_INTERVAL
        )
        # cli 视图：gap ≤ 60 → live
        assert cli_state == "live", f"{persona} gap={gap}s 应判 live，got {cli_state}"
        # server 视图：同源字段（last_waker_poll_at 距今 51s）→ ok（非 stale）
        # server `build_waker_heartbeats` 用 `threshold_minutes=15`（= 900s），51s << 900s → 不 stale
        server_threshold_seconds = 15 * 60
        server_state_ok = gap < server_threshold_seconds
        assert server_state_ok, f"{persona} gap={gap}s 应 ok（< {server_threshold_seconds}s server threshold）"
