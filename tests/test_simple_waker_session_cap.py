"""simple-waker session 硬上限测试（实验 bccb59ea A4）。

钉住验收：
- 同一 runtime session 连续成功唤醒达到阈值（默认
  ``SESSION_MAX_WAKES_DEFAULT``=300；``MAP_WAKER_SESSION_MAX_WAKES`` env 或
  CLI ``--session-max-wakes`` 覆盖）→ 唤醒前强制 ``reset_session()``，
  ``session_wake_count`` 归零，``stats.session_resets_wake_limit`` 置 1。
- 未达阈值 / 无旧会话（``claude_session_id`` 缺失）/ 显式 ``<=0`` → 不重置。
- 每次成功唤醒 ``session_wake_count`` +1（与 session 生命周期绑定）。
- ``resolve_session_max_wakes`` precedence：CLI flag > env > None（默认）。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

simple_waker = importlib.import_module("cli.simple_waker")
session_cap = importlib.import_module("cli.waker_session_cap")
MapCommandClient = importlib.import_module("cli.map_command_client").MapCommandClient
SimpleWaker = simple_waker.SimpleWaker
SimpleWakerConfig = simple_waker.SimpleWakerConfig

MAX_WAKES_ENV = session_cap.SESSION_MAX_WAKES_ENV


class FakeMapClient(MapCommandClient):
    def __init__(self, *, persona: str, todos: dict[str, Any]) -> None:
        self.persona = persona
        self._todos = todos

    def whoami(self) -> dict[str, Any]:
        return {"id": f"{self.persona}-agent", "name": self.persona}

    def todos(self) -> dict[str, Any]:
        return self._todos

    def work(self) -> dict[str, Any]:
        return {
            "agent": self.whoami(),
            "topic_progress": {"items": [], "total": 0},
            "todos": self._todos,
            "notifications": {"items": [], "total": 0, "unread_count": 0},
        }

    def agent_heartbeat(self, *, busy_since: Any) -> None:
        return None

    def inbound_event_record(
        self,
        *,
        event_id: str,
        fingerprint: str,
        event_type: str,
        source: str = "polling",
    ) -> bool:
        return True

    def experiment_scan_stalled_locks(self) -> dict[str, Any]:
        # host run_once 会 getattr 此方法；不 stub 会落到真实继承实现并触发
        # _base_args 的 self.map_cmd（与本测试无关）。空结果即可。
        return {"notification_ids": [], "emitted_count": 0}


def _waker(tmp_path: Path, *, session_max_wakes: int | None = None) -> tuple[SimpleWaker, MagicMock]:
    # todos 用桶键形状（summarize_pending_work 契约），确保 has_work=True 走 remind 路径。
    client = FakeMapClient(
        persona="host",
        todos={"pending_topic_replies": [{"comment_id": "c1"}]},
    )
    backend = MagicMock()
    backend.wake_async = AsyncMock(return_value=MagicMock(session_id="sess-new", skipped=False))
    backend.reset_session = AsyncMock()
    backend.connect = AsyncMock()
    backend.disconnect = AsyncMock()
    config = SimpleWakerConfig(
        persona="host",
        project_root=tmp_path,
        state_file=tmp_path / "state.json",
        session_max_wakes=session_max_wakes,
    )
    return SimpleWaker(client=client, config=config, backend=backend), backend


# --- resolve_session_max_wakes precedence ------------------------------------


def test_resolve_precedence_cli_over_env_over_default(monkeypatch) -> None:
    monkeypatch.delenv(MAX_WAKES_ENV, raising=False)
    # 无 CLI 无 env → None（调用方取默认）
    assert session_cap.resolve_session_max_wakes(None) is None
    # env 生效
    monkeypatch.setenv(MAX_WAKES_ENV, "50")
    assert session_cap.resolve_session_max_wakes(None) == 50
    # CLI 优先于 env
    assert session_cap.resolve_session_max_wakes(7) == 7
    # 非法 env → None（回退默认）
    monkeypatch.setenv(MAX_WAKES_ENV, "not-a-number")
    assert session_cap.resolve_session_max_wakes(None) is None
    # 显式 0（关闭）透传
    assert session_cap.resolve_session_max_wakes(0) == 0


# --- wake 计数与阈值行为 ------------------------------------------------------


def test_wake_count_increments_on_success(tmp_path: Path) -> None:
    waker, backend = _waker(tmp_path)
    persona_state = waker._persona_state("host")
    persona_state["claude_session_id"] = "old-session"
    persona_state["session_wake_count"] = 0

    stats = waker.run_once()

    backend.reset_session.assert_not_called()
    backend.wake_async.assert_awaited_once()
    assert stats.reminds_sent == 1
    assert persona_state["session_wake_count"] == 1


def test_wake_limit_reached_resets_session_before_wake(tmp_path: Path) -> None:
    waker, backend = _waker(tmp_path, session_max_wakes=5)
    persona_state = waker._persona_state("host")
    persona_state["claude_session_id"] = "old-session"
    persona_state["session_wake_count"] = 5  # 达到阈值

    stats = waker.run_once()

    backend.reset_session.assert_awaited_once()
    backend.wake_async.assert_awaited_once()
    assert stats.session_resets_wake_limit == 1
    assert stats.reminds_sent == 1
    # 重置后计数归零，且本次成功唤醒 +1（reset 先于 wake）
    assert persona_state["session_wake_count"] == 1


def test_below_limit_keeps_session(tmp_path: Path) -> None:
    waker, backend = _waker(tmp_path, session_max_wakes=5)
    persona_state = waker._persona_state("host")
    persona_state["claude_session_id"] = "old-session"
    persona_state["session_wake_count"] = 4  # 未达阈值

    stats = waker.run_once()

    backend.reset_session.assert_not_called()
    assert stats.session_resets_wake_limit == 0
    assert persona_state["session_wake_count"] == 5


def test_no_existing_session_skips_cap_check(tmp_path: Path) -> None:
    """无旧会话（首次唤醒）→ 无需重置，直接开新会话。"""
    waker, backend = _waker(tmp_path, session_max_wakes=5)
    persona_state = waker._persona_state("host")
    persona_state.pop("claude_session_id", None)
    persona_state["session_wake_count"] = 999  # 即使计数残留也不触发

    stats = waker.run_once()

    backend.reset_session.assert_not_called()
    assert stats.session_resets_wake_limit == 0
    assert stats.reminds_sent == 1


def test_zero_disables_cap(tmp_path: Path) -> None:
    """显式 session_max_wakes<=0 → 关闭硬上限，即使计数已超默认值。"""
    waker, backend = _waker(tmp_path, session_max_wakes=0)
    persona_state = waker._persona_state("host")
    persona_state["claude_session_id"] = "old-session"
    persona_state["session_wake_count"] = 10_000

    stats = waker.run_once()

    backend.reset_session.assert_not_called()
    assert stats.session_resets_wake_limit == 0


def test_env_override_applies(tmp_path: Path, monkeypatch) -> None:
    """env MAP_WAKER_SESSION_MAX_WAKES 在 config 未显式设置时生效。"""
    monkeypatch.setenv(MAX_WAKES_ENV, "2")
    waker, backend = _waker(tmp_path, session_max_wakes=None)
    persona_state = waker._persona_state("host")
    persona_state["claude_session_id"] = "old-session"
    persona_state["session_wake_count"] = 2

    stats = waker.run_once()

    backend.reset_session.assert_awaited_once()
    assert stats.session_resets_wake_limit == 1


def test_default_cap_is_session_max_wakes_default(tmp_path: Path) -> None:
    """未显式配置时阈值 = SESSION_MAX_WAKES_DEFAULT（300）。"""
    waker, backend = _waker(tmp_path, session_max_wakes=None)
    persona_state = waker._persona_state("host")
    persona_state["claude_session_id"] = "old-session"
    persona_state["session_wake_count"] = session_cap.SESSION_MAX_WAKES_DEFAULT

    stats = waker.run_once()

    backend.reset_session.assert_awaited_once()
    assert stats.session_resets_wake_limit == 1


def test_reset_then_wake_keeps_state_dirty_saved(tmp_path: Path) -> None:
    """达阈值重置后 state 文件应持久化归零计数（下一进程读到正确状态）。"""
    import json

    waker, backend = _waker(tmp_path, session_max_wakes=3)
    persona_state = waker._persona_state("host")
    persona_state["claude_session_id"] = "old-session"
    persona_state["session_wake_count"] = 3

    waker.run_once()

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    host_state = state.get("personas", {}).get("host", state)
    assert int(host_state.get("session_wake_count", -1)) == 1
