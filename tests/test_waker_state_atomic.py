"""Waker state.json writer atomic write (实验 d12c328c A4)。

``cli/bridge_state.py:save_bridge_state`` 走 tmp + os.replace (POSIX atomic
rename)；本文件覆盖 writer 侧的契约：

- (a) tmp 文件先写到 ``state.json.tmp``，再 ``os.replace`` 到目标 ——
  任何时刻 state.json 要么是旧内容要么是新内容，不会出现半截文件
  （除非 reader 在两次 syscall 之间撞见 .tmp——read retry 兜底，见
  test_waker_status_view.py case (f)）。
- (b) ``os.replace`` 是 atomic rename — 观察 .tmp 不应残留到目标路径
  之外的副作用。
- (c) 并发写：tmp + replace 在单进程内串行安全（实际场景 waker 单进程
  写自己 persona 状态，无跨进程竞争）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cli.bridge_state import save_bridge_state  # noqa: E402


def test_a_atomic_write_writes_via_tmp_then_replace(tmp_path: Path) -> None:
    """Case (a): state.json 写入走 tmp + os.replace，不留半截文件。"""
    state_path = tmp_path / "simple-waker-state-host.json"
    state_path.write_text(json.dumps({"old": True, "schema_version": 1}))

    new_state = {"schema_version": 1, "personas": {"host": {"pid": os.getpid()}}}
    save_bridge_state(state_path, new_state)

    # 主路径：完整新内容 + 旧内容已替换
    final = json.loads(state_path.read_text())
    assert final["personas"]["host"]["pid"] == os.getpid()
    assert "old" not in final


def test_b_atomic_write_does_not_leak_tmp_after_replace(tmp_path: Path) -> None:
    """Case (b): 写完成后 state.json.tmp 不应残留（os.replace 已吞掉 tmp）。"""
    state_path = tmp_path / "simple-waker-state-host.json"
    state_path.write_text(json.dumps({"old": True, "schema_version": 1}))

    save_bridge_state(state_path, {"schema_version": 1, "personas": {}})

    # tmp 文件应已被 replace 清掉
    assert not (state_path.with_suffix(state_path.suffix + ".tmp")).exists()


def test_c_atomic_write_consecutive_writes_serial_safe(tmp_path: Path) -> None:
    """Case (c): 连续两次写，第二份完整覆盖第一份，无半截态。"""
    state_path = tmp_path / "simple-waker-state-host.json"
    for cycle in range(5):
        save_bridge_state(
            state_path,
            {"schema_version": 1, "personas": {"host": {"cycles_total": cycle}}},
        )
        # 每次写完读者立即能解析（无半截）
        parsed = json.loads(state_path.read_text())
        assert parsed["personas"]["host"]["cycles_total"] == cycle


def test_d_atomic_write_creates_parent_directories(tmp_path: Path) -> None:
    """Case (d): 父目录不存在 → save_bridge_state 应自动 mkdir(parents=True)。"""
    state_path = tmp_path / "nested" / "deep" / "state.json"
    save_bridge_state(state_path, {"schema_version": 1, "personas": {}})
    assert state_path.exists()
    assert json.loads(state_path.read_text()) == {"schema_version": 1, "personas": {}}
