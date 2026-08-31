"""Waker status view d12c328c 修复 fixture (实验 d12c328c I6)。

本文件覆盖 waker busy 档真实同源 server 30min 容忍 + 验收模板硬约束后的
回归测试 (实验 d12c328c I6 ≥6 case)。与 test_waker_status.py (T6 a8b64c20
派生档位表) 互补——本文件聚焦 d12c328c 修复点:

- (a) busy 5min fixture busy_started_at = now()-300s + pid 存活 + poll 暂停
  → live（非 stale/dead；修复前 busy_stale = idle_stale_w ≈ 180s 偷换语义
  → busy 300s 已升级 stale）
- (b) 超阈值 fixture busy_started_at = now()-1900s → stale（> 30min 容忍）
- (c) 缺字段 fixture state.json 完全删掉 expected_remind_runtime_seconds
  → fallback 30min default（不偷换 idle_stale_w）
- (d) pid zombie fixture mock defunct → stale（I4 排除 defunct pid）
- (e) fallback chain 三层: state.json / env / 都没有 → 都返回 30min
- (f) atomic write race mock 半截 JSON → CLI _load_state_file 重试一次成功

mock datetime / monkeypatch 注入 busy_started_at；mock pid zombie 用
unittest.mock.patch 拦截 _is_zombie + open()。freezegun 不用（避免新依赖）。
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

waker_view = importlib.import_module("cli.waker_status_view")
compute_waker_state = waker_view.compute_waker_state
_resolve_expected_remind_runtime = waker_view._resolve_expected_remind_runtime
_pid_alive = waker_view._pid_alive
_is_zombie = waker_view._is_zombie
_load_state_file = waker_view._load_state_file

EXPECTED_REMIND_RUNTIME_SECONDS = 30 * 60  # 1800s = 30min default
NOW = datetime(2026, 8, 31, 1, 0, 0, tzinfo=timezone.utc)


# =========================================================================
# Helpers
# =========================================================================


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _state(
    *,
    pid: int | None = os.getpid(),
    busy_started_at: datetime | None = None,
    last_poll_gap_s: float | None = 2.0,
    expected_remind_runtime_seconds: int | None = EXPECTED_REMIND_RUNTIME_SECONDS,
) -> dict:
    """Build persona state dict with busy_started_at + expected_remind_runtime."""
    state: dict = {}
    if pid is not None:
        state["pid"] = pid
    if last_poll_gap_s is not None:
        state["last_poll_at"] = _iso(NOW - timedelta(seconds=last_poll_gap_s))
    if busy_started_at is not None:
        state["busy_started_at"] = _iso(busy_started_at)
    if expected_remind_runtime_seconds is not None:
        state["expected_remind_runtime_seconds"] = expected_remind_runtime_seconds
    return state


# =========================================================================
# Case (a) busy 5min fixture：busy 300s + pid 存活 → live（非 stale/dead）
# 修复前：busy_stale_w = max(idle_stale_w=300, 2*idle_stale_w=600) = 600
#   busy_age = 300 < 600 → 不升级；走普通 gap 判定 gap=2 ≤ 60 → live
# 修复后：busy_stale_w = max(1800, 2*300) = 1800（与 server 30min 容忍同源）
#   busy_age = 300 < 1800 → 不升级；走普通 gap 判定 → live
# 验证：busy 5min（典型 host 长会话）不再被错报 stale；busy_stale_w 不再偷换 idle_stale。
# =========================================================================


def test_case_a_busy_5min_within_tolerance_stays_live() -> None:
    """Case (a): busy_started_at = now()-300s + pid 存活 + poll 暂停 → live。"""
    state = _state(
        busy_started_at=NOW - timedelta(seconds=300),
        last_poll_gap_s=2.0,
    )
    assert compute_waker_state(state, now=NOW) == "live"


# =========================================================================
# Case (b) 超阈值 fixture：busy_started_at = now()-1900s → stale（> 30min 容忍）
# 修复后：busy_stale_w = 1800，busy_age = 1900 > 1800 → 升级 stale（卡死 busy）
# =========================================================================


def test_case_b_busy_over_tolerance_promoted_to_stale() -> None:
    """Case (b): busy 1900s（> 30min server 容忍）→ 升级 stale（卡死 busy 守卫）。"""
    state = _state(
        busy_started_at=NOW - timedelta(seconds=1900),
        last_poll_gap_s=2.0,  # poll 仍新，但 busy 卡死，busy_age 守卫优先
    )
    assert compute_waker_state(state, now=NOW) == "stale"


# =========================================================================
# Case (c) 缺字段 fixture：state.json 完全删掉 expected_remind_runtime_seconds
# → fallback 30min default（不偷换 idle_stale_w；旧 idle_stale = 300s ≈ 5min）
# 验证：fallback 是 1800s（30min）不是 idle_stale_w=300s；防止再次偷换。
# =========================================================================


def test_case_c_missing_field_falls_back_to_30min_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Case (c): state.json 缺 expected_remind_runtime_seconds + env 也无 → 30min default。"""
    # 隔离 env
    monkeypatch.delenv("MAP_EXPECTED_REMIND_RUNTIME_MINUTES", raising=False)
    state = _state(
        busy_started_at=NOW - timedelta(seconds=600),  # busy 10min
        last_poll_gap_s=2.0,
        expected_remind_runtime_seconds=None,  # 缺字段
    )
    result = compute_waker_state(state, now=NOW)
    # busy_stale_w = max(1800, 2*300) = 1800（fallback 30min）
    # busy_age = 600 < 1800 → 不升级；gap=2 ≤ 60 → live
    assert result == "live"
    # 显式核验 fallback 链返回的就是 30min（1800s），不是 idle_stale=300s
    resolved = _resolve_expected_remind_runtime({})
    assert resolved == EXPECTED_REMIND_RUNTIME_SECONDS, (
        f"fallback must be 1800s (30min default), got {resolved}"
    )


# =========================================================================
# Case (d) pid zombie fixture：mock defunct pid → stale
# 修复前：_pid_alive 仅靠 os.kill(pid, 0)；defunct pid 通过 → 误判存活 → 走 busy_age 判定
# 修复后：_pid_alive + _is_zombie（读 /proc/<pid>/status State）→ defunct 排除 → 升级 stale/dead
# =========================================================================


def test_case_d_pid_zombie_promoted_to_stale() -> None:
    """Case (d): mock defunct pid → _is_zombie 返回 True → 升级 stale。"""
    state = _state(
        busy_started_at=NOW - timedelta(seconds=600),  # busy 10min（容忍内）
        last_poll_gap_s=2.0,
    )
    with patch.object(waker_view, "_is_zombie", return_value=True):
        result = compute_waker_state(state, now=NOW)
    # defunct pid → _pid_alive 返回 False → dead（pid 缺失或已死分支）
    assert result == "dead", (
        f"defunct pid should trigger 'dead' (pid missing/alive=False branch), got {result}"
    )


def test_case_d2_pid_zombie_via_proc_status_mock(tmp_path: Path) -> None:
    """Case (d) 真实路径：fake /proc/<pid>/status 文件注入 Z state → _is_zombie 返回 True。"""
    # mock _is_zombie 与 os.kill 双通过；通过 patch 注入 fake proc status 路径
    fake_proc_dir = tmp_path / "proc"
    fake_proc_dir.mkdir()
    fake_pid = 12345
    (fake_proc_dir / str(fake_pid)).mkdir()
    (fake_proc_dir / str(fake_pid) / "status").write_text(
        "Name:\tfake\nState:\tZ (zombie)\nPid:\t12345\n"
    )

    def fake_is_zombie(pid: int) -> bool:
        try:
            return (fake_proc_dir / str(pid) / "status").read_text().startswith(
                "Name:\tfake\nState:\tZ"
            )
        except OSError:
            return False

    state = _state(
        pid=fake_pid,
        busy_started_at=NOW - timedelta(seconds=60),
        last_poll_gap_s=2.0,
    )
    with (
        patch.object(waker_view, "_is_zombie", side_effect=fake_is_zombie),
        patch("os.kill", return_value=None),  # fake process "alive"
    ):
        result = compute_waker_state(state, now=NOW)
    assert result == "dead"


# =========================================================================
# Case (e) fallback chain 三层：state.json / env / 都没有 → 都返回 30min
# 优先级: state.json > env > 30min default
# 验证：缺字段、env 设值、env 非法、env 设值后 state.json 也覆盖 → 都符合预期
# =========================================================================


def test_case_e1_fallback_chain_state_json_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Case (e) 第 1 层：state.json.expected_remind_runtime_seconds 覆盖 env。"""
    monkeypatch.setenv("MAP_EXPECTED_REMIND_RUNTIME_MINUTES", "60")  # env 3600s
    state_val = 600  # state.json 600s（10min）— 优先级更高
    resolved = _resolve_expected_remind_runtime(
        {"expected_remind_runtime_seconds": state_val}
    )
    assert resolved == state_val, "state.json should beat env"


def test_case_e2_fallback_chain_env_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Case (e) 第 2 层：state.json 缺字段 + env 设值 → env 接管。"""
    monkeypatch.setenv("MAP_EXPECTED_REMIND_RUNTIME_MINUTES", "45")
    resolved = _resolve_expected_remind_runtime({})
    assert resolved == 45 * 60, f"env 45min → 2700s, got {resolved}"


def test_case_e3_fallback_chain_default_when_nothing_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Case (e) 第 3 层：state.json 缺字段 + env 无 → 30min default（1800s）。"""
    monkeypatch.delenv("MAP_EXPECTED_REMIND_RUNTIME_MINUTES", raising=False)
    resolved = _resolve_expected_remind_runtime({})
    assert resolved == EXPECTED_REMIND_RUNTIME_SECONDS


def test_case_e4_fallback_chain_env_invalid_falls_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Case (e) 边界：env 值非法（不能 int()）→ RuntimeWarning + fallback 30min。"""
    import warnings as _warnings

    monkeypatch.setenv("MAP_EXPECTED_REMIND_RUNTIME_MINUTES", "not-a-number")
    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        resolved = _resolve_expected_remind_runtime({})
    assert resolved == EXPECTED_REMIND_RUNTIME_SECONDS, (
        f"env invalid → fallback 30min, got {resolved}"
    )
    assert any(
        issubclass(w.category, RuntimeWarning)
        and "MAP_EXPECTED_REMIND_RUNTIME_MINUTES" in str(w.message)
        for w in caught
    ), "should emit RuntimeWarning on invalid env"


# =========================================================================
# Case (f) atomic write race：mock 半截 JSON → CLI _load_state_file 重试一次成功
# 验证：write 半截文件 → reader 第一次 fail → 第二次（重试）成功
# =========================================================================


def test_case_f_atomic_write_race_reader_retries_success(tmp_path: Path) -> None:
    """Case (f): state.json 写入瞬间半截 JSON → _load_state_file 重试一次成功。

    Path.read_text 是 pathlib 内置 C 实现，mock.patch 无法拦截；改用
    实际文件竞态：先写半截 → patch json.loads 在第一次 fail 时把完整 JSON
    atomic replace 到磁盘（模拟 writer 第二次 retry 前完成）→ 第二次 read_text
    拿到完整 → 解析成功。
    """
    state_path = tmp_path / "simple-waker-state-host.json"
    valid_payload = json.dumps(
        {
            "schema_version": 1,
            "personas": {
                "host": {
                    "pid": os.getpid(),
                    "started_at": _iso(NOW - timedelta(minutes=5)),
                    "last_poll_at": _iso(NOW - timedelta(seconds=2)),
                    "expected_remind_runtime_seconds": EXPECTED_REMIND_RUNTIME_SECONDS,
                }
            },
        },
        ensure_ascii=False,
    )
    half_json = valid_payload[: len(valid_payload) // 2]

    # 先写半截（模拟 atomic write 期间读者撞见旧 tmp）
    state_path.write_text(half_json)

    loads_calls = {"count": 0}
    original_loads = json.loads

    def racing_loads(payload, *args, **kwargs):  # type: ignore[no-untyped-def]
        loads_calls["count"] += 1
        if loads_calls["count"] == 1:
            # 第一次解析前（payload=half_json）→ atomic replace 完整 JSON 到磁盘
            # （_load_state_file 的下一次循环会重新 read_text）
            state_path.write_text(valid_payload)
        return original_loads(payload, *args, **kwargs)

    with patch("json.loads", side_effect=racing_loads):
        result = _load_state_file(state_path)

    assert loads_calls["count"] == 2, (
        f"reader must retry once on JSONDecodeError, got {loads_calls['count']}"
    )
    assert isinstance(result, dict)
    assert result.get("pid") == os.getpid()
    assert result.get("expected_remind_runtime_seconds") == EXPECTED_REMIND_RUNTIME_SECONDS


def test_case_f2_atomic_write_race_persistent_corruption_returns_empty(tmp_path: Path) -> None:
    """Case (f) 边界：连续两次都半截（持久损坏）→ 返回 {}（视图层降级为 never）。"""
    state_path = tmp_path / "simple-waker-state-host.json"
    state_path.write_text('{"personas":')  # 永久半截

    import warnings as _warnings

    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        result = _load_state_file(state_path)

    assert result == {}
    assert any(
        issubclass(w.category, RuntimeWarning) and "unparseable" in str(w.message)
        for w in caught
    ), "should emit RuntimeWarning on persistent corruption"


# =========================================================================
# 集成验收：3-waker 全场景 — busy 5min 三 persona 不一致判定（fixture 全 mock）
# 验证：host/participant/reviewer 三 persona busy 5min → 全 live（与 server 30min 容忍一致）
# =========================================================================


def test_acceptance_three_waker_busy_5min_consistent() -> None:
    """§A8 3-waker smoke fixture 覆盖：3 persona busy 5min 全 live。"""
    for persona in ("host", "participant", "reviewer"):
        state = _state(
            pid=os.getpid(),
            busy_started_at=NOW - timedelta(seconds=300),
            last_poll_gap_s=2.0,
        )
        result = compute_waker_state(state, now=NOW)
        assert result == "live", (
            f"{persona} busy 5min should stay live (server 30min tolerance), got {result}"
        )


# =========================================================================
# T9-B 修复 fixture（实验 b01d3944 I2）：穿透修复 + fixture 归真 5 case 回归
# =========================================================================
#
# 现象（实测）：T9 修复后真实 3-waker 环境 host busy 624s + last_poll 冻结在
# busy 起点 → busy_age 穿透为 gap → 误报 dead。T9 case (a) fixture
# last_poll_gap_s=2.0 编码「poll 正常」语义，恰好绕过穿透路径。
#
# T9-B 修复：在 compute_waker_state A2 分支加 busy 短路——busy_since 有效 +
# pid 存活 + busy_age ≤ busy_stale_w → busy（不走 gap 判定）；busy_age >
# busy_stale_w → stale；pid 缺失/已死（含 defunct）→ dead。
#
# T9-B fixture 修正：last_poll_at = busy_started_at（poll 冻结）真实编码
# busy 期间 poll 暂停语义，5 case 覆盖完整边界：
# - (a) busy 2s + poll 冻结 → busy（与 T9 case (a) busy 300s+新鲜 poll 对比）
# - (b) busy 600s + poll 冻结 → busy（KEY 修复测试：busy 穿透回归 1）
# - (c) busy 1860s + poll 冻结 → stale（busy 超阈值升级）
# - (d) busy 600s + poll 冻结 + pid defunct → dead（zombie 优先级）
# - (e) 旧 state 兼容：state.json 无 busy_started_at → 维持原 gap 判定


def _state_poll_frozen(
    *,
    pid: int | None = os.getpid(),
    busy_age_s: float,
    is_defunct: bool = False,
) -> dict:
    """Build persona state with busy_since + last_poll_at = busy_started_at (poll frozen)."""
    busy_started_at = NOW - timedelta(seconds=busy_age_s)
    state: dict = {}
    if pid is not None:
        state["pid"] = pid
    state["busy_started_at"] = _iso(busy_started_at)
    state["last_poll_at"] = _iso(busy_started_at)  # poll 冻结在 busy 起点
    state["expected_remind_runtime_seconds"] = EXPECTED_REMIND_RUNTIME_SECONDS
    # 注：is_defunct 由 caller 通过 patch._is_zombie 注入
    _ = is_defunct
    return state


# =========================================================================
# T9-B Case (a): busy 2s + poll 冻结 → busy（与 T9 case (a) busy 300s 对比）
# =========================================================================


def test_t9b_case_a_busy_2s_poll_frozen_is_busy() -> None:
    """T9-B Case (a): busy_age=2s + last_poll_at=busy_started_at + pid alive → busy。

    与 T9 case (a)（busy 300s + last_poll_gap_s=2.0）形成对比：
    - T9 case (a) 模拟「poll 正常」（gap=2s → live via gap judgment）
    - T9-B case (a) 模拟「真实 busy 期间 poll 冻结」→ busy（短路返回）
    """
    state = _state_poll_frozen(busy_age_s=2)
    assert compute_waker_state(state, now=NOW) == "busy"


# =========================================================================
# T9-B Case (b): busy 600s + poll 冻结 → busy（KEY 修复测试）
# =========================================================================


def test_t9b_case_b_busy_600s_poll_frozen_stays_busy_not_dead() -> None:
    """T9-B Case (b): busy 600s + poll 冻结 + pid alive → busy（非 dead）。

    这是 T9-B 的 KEY 测试：修复前 busy 600s 穿透到 gap 判定 → dead；
    修复后 busy 短路 → busy。
    """
    state = _state_poll_frozen(busy_age_s=600)
    result = compute_waker_state(state, now=NOW)
    assert result == "busy", (
        f"busy 600s + poll 冻结 + pid alive must short-circuit to busy, got {result}"
    )


# =========================================================================
# T9-B Case (c): busy 1860s + poll 冻结 → stale（超阈值升级）
# =========================================================================


def test_t9b_case_c_busy_1860s_poll_frozen_escalates_to_stale() -> None:
    """T9-B Case (c): busy_age=1860s + poll 冻结 + pid alive → stale。

    busy_stale_w=1800s（30min default），busy_age=1860 > 1800 → 升级 stale。
    """
    state = _state_poll_frozen(busy_age_s=1860)
    assert compute_waker_state(state, now=NOW) == "stale"


# =========================================================================
# T9-B Case (d): busy 600s + poll 冻结 + pid defunct → dead（zombie 优先级）
# =========================================================================


def test_t9b_case_d_pid_defunct_during_busy_is_dead() -> None:
    """T9-B Case (d): busy 600s + poll 冻结 + pid defunct → dead。

    zombie 优先级高于 busy 短路：defunct pid → _pid_alive=False → dead。
    避免「defunct pid 误报 busy」bug（participant §2 提议）。
    """
    state = _state_poll_frozen(busy_age_s=600)
    with patch.object(waker_view, "_is_zombie", return_value=True):
        result = compute_waker_state(state, now=NOW)
    assert result == "dead", (
        f"defunct pid during busy must trigger dead (zombie priority), got {result}"
    )


# =========================================================================
# T9-B Case (e): 旧 state 兼容 — 无 busy_started_at → 维持原 gap 判定
# =========================================================================


def test_t9b_case_e_legacy_state_without_busy_started_at() -> None:
    """T9-B Case (e): state.json 无 busy_started_at → 维持原 gap 判定行为不变。

    向后兼容：旧 state 行为不变（不进入 busy 分支；走 pid + gap 判定）。
    busy_started_at 缺 + last_poll fresh → live。
    """
    state = _state(
        pid=os.getpid(),
        busy_started_at=None,
        last_poll_gap_s=2.0,
    )
    # 无 busy_started_at → 跳过 busy 短路分支；走 gap=2 ≤ live_w=60 → live
    assert compute_waker_state(state, now=NOW) == "live"


# =========================================================================
# T9-B Case (f): T9 case (a) 保留对比 — busy 300s + fresh poll → live
# =========================================================================


def test_t9b_case_f_busy_300s_with_fresh_poll_stays_live() -> None:
    """T9-B Case (f): busy 300s + last_poll fresh (2s ago) + pid alive → live。

    与 T9 case (a) 一致：poll 在 busy 期间发生过 → waker 仍活跃 → live。
    此 case 证明 T9-B 修复未破坏 T9 已修的 busy 档（fresh poll 仍 live）。
    """
    state = _state(
        pid=os.getpid(),
        busy_started_at=NOW - timedelta(seconds=300),
        last_poll_gap_s=2.0,
    )
    # busy_age=300 ≤ busy_stale_w=1800；last_poll(now-2) >= busy_since(now-300) → live
    assert compute_waker_state(state, now=NOW) == "live"
