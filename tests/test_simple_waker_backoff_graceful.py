"""Tests for T39: waker exponential backoff + graceful shutdown.

T39 ships two behaviours in ``cli/simple_waker.py``:

1. **指数退避** — when a cycle fails with ``WorkerError`` (API 5xx /
   subprocess failure / identity resolution failure), the long-running
   waker must not retry on a fixed 300s cadence. Instead it backs off
   exponentially: ``idle_interval × 2^n`` capped at 30 minutes, and
   ``_consecutive_errors`` resets to 0 on the first successful cycle.
2. **优雅退出** — ``SIGTERM`` / ``SIGINT`` register a signal handler
   (``loop.add_signal_handler``) that sets a stop flag and wakes an
   ``asyncio.Event``, so the currently blocked sleep (potentially up to
   the 30-minute backoff cap) returns immediately. The loop then breaks
   after the current cycle and runs ``finally: backend.disconnect()``,
   which the previous bare ``KeyboardInterrupt`` path did not guarantee.
"""

from __future__ import annotations

import asyncio
import importlib
import signal
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

simple_waker = importlib.import_module("cli.simple_waker")
from cli.errors import WorkerError  # noqa: E402

SimpleWaker = simple_waker.SimpleWaker
SimpleWakerConfig = simple_waker.SimpleWakerConfig


def _make_waker(
    *,
    config_kwargs: dict[str, Any] | None = None,
) -> SimpleWaker:
    kwargs = {
        "persona": "host",
        "idle_interval": 300.0,
        "state_file": Path(".map/t39-state.json"),
    }
    if config_kwargs:
        kwargs.update(config_kwargs)
    backend = MagicMock()
    backend.connect = AsyncMock()
    backend.disconnect = AsyncMock()
    backend.wake_async = AsyncMock()
    backend.reset_session = AsyncMock()
    return SimpleWaker(
        client=MagicMock(),
        config=SimpleWakerConfig(**kwargs),
        backend=backend,
    )


# ---------------------------------------------------------------------------
# _backoff_interval: 指数退避（纯函数）
# ---------------------------------------------------------------------------


def test_backoff_interval_escalates_and_caps() -> None:
    """idle×2^n,cap 30min;连续失败次数越多间隔越长。"""
    waker = _make_waker(config_kwargs={"idle_interval": 300.0})
    waker._consecutive_errors = 1
    assert waker._backoff_interval() == 600.0  # 5min → 10min
    waker._consecutive_errors = 2
    assert waker._backoff_interval() == 1200.0  # 10min → 20min
    waker._consecutive_errors = 3
    assert waker._backoff_interval() == 1800.0  # 30min cap
    waker._consecutive_errors = 100
    assert waker._backoff_interval() == 1800.0  # 仍 cap 在 30min


def test_backoff_interval_uses_idle_interval_scale() -> None:
    """idle_interval 可配(如 60s→120s→240s)。"""
    waker = _make_waker(config_kwargs={"idle_interval": 60.0})
    waker._consecutive_errors = 0
    assert waker._backoff_interval() == 60.0
    waker._consecutive_errors = 1
    assert waker._backoff_interval() == 120.0


# ---------------------------------------------------------------------------
# _request_stop: 信号置位 + 唤醒 sleep
# ---------------------------------------------------------------------------


def test_request_stop_sets_flag_and_wakes_event() -> None:
    """SIGTERM/SIGINT 回调→置 stop 标志并 set event(打断最长 30min 睡眠)。"""
    waker = _make_waker()
    waker._stop_event = asyncio.Event()
    assert waker._stop_requested is False
    waker._request_stop()
    assert waker._stop_requested is True
    assert waker._stop_event.is_set() is True


def test_request_stop_is_idempotent() -> None:
    """重复信号不重复 set(幂等)。"""
    waker = _make_waker()
    waker._stop_event = asyncio.Event()
    waker._request_stop()
    waker._request_stop()
    assert waker._stop_requested is True
    assert waker._stop_event.is_set() is True


# ---------------------------------------------------------------------------
# _run_forever_async: 连续失败退避 + 优雅退出
# ---------------------------------------------------------------------------


def _patch_run_once(
    waker: SimpleWaker,
    *,
    failures: int,
    success_sleep: float = 0.001,
) -> None:
    """monkeypatch ``_run_once_async``:前 ``failures`` 次抛 WorkerError,
    之后返回成功(空 stats + 受控小 sleep),隔离验证循环层退避/复位逻辑。
    失败分支的 sleep_for 由循环层 ``_backoff_interval()`` 决定。"""
    call = {"n": 0}

    async def fake_run_once() -> tuple[Any, float]:
        call["n"] += 1
        if call["n"] <= failures:
            raise WorkerError("transient 5xx")
        return simple_waker.SimpleWakerStats(cycles=1), success_sleep

    waker._run_once_async = fake_run_once  # type: ignore[method-assign]


def test_run_forever_failure_increments_then_success_resets() -> None:
    """循环层:失败 cycle→cycle_errors+1 且 _consecutive_errors 递增;
    后续成功 cycle 复位 _consecutive_errors=0。使用极小 idle 让退避睡眠
    几乎即刻返回,自然跑完 max_cycles。"""
    waker = _make_waker(
        config_kwargs={
            "idle_interval": 0.01,  # 退避 0.02/0.04… 即刻返回
            "max_cycles": 4,  # 1 失败 + 3 成功
        }
    )
    _patch_run_once(waker, failures=1)
    # 让 _backoff_interval 的调用可观测。
    backoff_calls: list[float] = []
    original_backoff = waker._backoff_interval

    def _spy_backoff() -> float:
        value = original_backoff()
        # 记录曲线但不阻塞:实际等待仍在 sleep_for 上,值极小。
        backoff_calls.append(value)
        return value

    waker._backoff_interval = _spy_backoff  # type: ignore[method-assign]

    stats = asyncio.run(waker._run_forever_async())

    assert stats.cycles == 4
    assert stats.cycle_errors == 1
    assert waker._consecutive_errors == 0  # 成功 cycle 后复位
    # 仅失败分支调用退避,且曲线符合 idle×2^n。
    assert backoff_calls == [0.02]


def test_run_forever_backoff_grows_across_multiple_failures() -> None:
    """连续多轮失败→退避曲线逐级翻倍,成功后才复位。"""
    waker = _make_waker(
        config_kwargs={
            "idle_interval": 1.0,
            "max_cycles": 3,  # 2 失败 + 1 成功
        }
    )
    _patch_run_once(waker, failures=2)
    backoff_calls: list[float] = []
    original_backoff = waker._backoff_interval

    def _spy_backoff() -> float:
        value = original_backoff()
        backoff_calls.append(value)
        return value

    waker._backoff_interval = _spy_backoff  # type: ignore[method-assign]

    stats = asyncio.run(waker._run_forever_async())

    assert stats.cycle_errors == 2
    assert waker._consecutive_errors == 0
    # 2 次失败 → 1×2^1=2、1×2^2=4
    assert backoff_calls == [2.0, 4.0]


def test_run_forever_breaks_on_stop_request_with_disconnect() -> None:
    """收到 _request_stop 后:当前 cycle 收尾→break→finally 调 disconnect;
    sleep 被 event 提前唤醒,不会空等最长退避间隔。"""
    waker = _make_waker(config_kwargs={"idle_interval": 300.0})
    _patch_run_once(waker, failures=1)
    result: dict[str, Any] = {}

    async def scenario() -> None:
        task = asyncio.create_task(waker._run_forever_async())
        # 让首 cycle(失败)完成并进入 600s 退避睡眠。
        await asyncio.sleep(0.05)
        # 模拟 SIGTERM 到达:置位 + 唤醒 sleep→循环 break→finally disconnect。
        waker._request_stop()
        result["stats"] = await task

    asyncio.run(scenario())

    waker.backend.disconnect.assert_awaited_once()
    assert result["stats"].cycles >= 1
    assert result["stats"].cycle_errors >= 1
    # 睡眠被事件打断而非空等 600s——能秒回即证明 wait_for+Event 生效。
    assert waker._stop_event is None  # finally 已清空


def test_run_forever_registers_and_cleans_up_signal_handlers() -> None:
    """add_signal_handler 注册 SIGTERM+SIGINT;finally 移除;dry_run 不连 backend。"""
    waker = _make_waker(config_kwargs={"idle_interval": 60.0, "once": True, "dry_run": True})
    registered: list[int] = []
    removed: list[int] = []
    original_add = loop_remove = None

    async def scenario() -> None:
        nonlocal original_add, loop_remove
        loop = asyncio.get_running_loop()
        original_add = loop.add_signal_handler
        loop_remove = loop.remove_signal_handler

        def fake_add(sig: int, cb) -> None:  # noqa: ANN001
            registered.append(sig)
            # 委托到真实实现保留回调,便于 remove 正常清理。
            original_add(sig, cb)

        def fake_remove(sig: int) -> Any:
            removed.append(sig)
            return loop_remove(sig)

        loop.add_signal_handler = fake_add  # type: ignore[method-assign]
        loop.remove_signal_handler = fake_remove  # type: ignore[method-assign]
        await waker._run_forever_async()

    asyncio.run(scenario())

    assert signal.SIGTERM in registered
    assert signal.SIGINT in registered
    assert signal.SIGTERM in removed
    assert signal.SIGINT in removed
    # dry_run 不 connect/disconnect。
    waker.backend.connect.assert_not_awaited()
    waker.backend.disconnect.assert_not_awaited()
