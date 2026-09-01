"""SimpleWaker 的状态持久化与 busy 心跳 mixin — T45 拆分自 cli/simple_waker.py。

busy 标记生命周期（``_touch_busy`` / ``_clear_busy`` / crash 回收）、
waker 自观测计数（``_accumulate_cycle_stats`` / 重启归档）与 state.json
落盘（``_save_state_if_needed``）。依赖宿主注入的 ``self._persona_state``
/ ``self.config`` / ``self.client``。
"""
from __future__ import annotations

import contextlib
import json
import os
from datetime import datetime, timezone
from typing import Any

import typer

from cli.bridge_state import save_bridge_state
from cli.errors import WorkerError
from cli.waker_context import SimpleWakerStats


class SimpleWakerStateMixin:
    """busy 心跳、自观测计数与 state.json 持久化。"""

    def _touch_busy(self, stats: SimpleWakerStats, *, now: datetime) -> None:
        """I2（A1）：标记本 waker 进入 busy session（remind → claude 调用）。

        写 waker state 文件 ``session_busy_since`` + ``busy_pid`` +
        ``busy_started_at``；PATCH server ``agents.last_busy_since``。
        失败兜底不阻塞主流程（心跳信号不能反过来拖死 remind）。
        """
        persona_state = self._persona_state(self.config.persona)
        persona_state["session_busy_since"] = now.isoformat()
        persona_state["busy_started_at"] = now.isoformat()
        persona_state["busy_pid"] = os.getpid()
        self._state_dirty = True
        self._save_state_if_needed(force=True)
        self._patch_server_busy(stats, busy_since=now)

    def _clear_busy(self, stats: SimpleWakerStats) -> None:
        """I2（A1）：与 ``_touch_busy`` 配对，会话结束清零 busy 标记。

        PID 自检：若 busy_pid != 当前 os.getpid() → 跨进程/重启场景，
        只清 server 列（让其他 worker 不被本地 stale 状态拖累），不动
        state 文件（避免误清别的进程 active busy 记录）。
        """
        persona_state = self._persona_state(self.config.persona)
        busy_pid = persona_state.get("busy_pid")
        own_pid = os.getpid()
        if busy_pid is not None and int(busy_pid) != own_pid:
            # 跨 PID：只清 server 列，本地 state 由 busy_pid 的进程负责。
            self._patch_server_busy(stats, busy_since=None)
            return
        for key in ("session_busy_since", "busy_started_at", "busy_pid"):
            persona_state.pop(key, None)
        self._state_dirty = True
        self._save_state_if_needed(force=True)
        self._patch_server_busy(stats, busy_since=None)

    # 实验 waker-status-view I2（A1+A3+A5）: 每个 cycle 自写 waker 状态
    # 指标（pid / cycles / reminds / skips / errors 滚动窗口），不引入
    # 新 IO（复用 _save_state_if_needed 路径 atomic write）。view 层派生
    # uptime/state/busy_since；本方法只写原始计数 + 时间戳。
    def _accumulate_cycle_stats(self, stats: SimpleWakerStats) -> None:
        persona_state = self._persona_state(self.config.persona)
        current_pid = os.getpid()
        now = datetime.now(timezone.utc)

        prev_pid = persona_state.get("pid")
        if prev_pid is not None and int(prev_pid) != current_pid:
            # A5: waker 重启（pid 变化）→ 归档旧计数 + 归零 cycles
            self._archive_and_reset_on_restart(
                persona_state, prev_pid=int(prev_pid), current_pid=current_pid, now=now
            )

        persona_state["pid"] = current_pid
        persona_state.setdefault("started_at", now.isoformat())

        persona_state["cycles_total"] = int(persona_state.get("cycles_total", 0)) + 1
        persona_state["reminds_sent_total"] = int(
            persona_state.get("reminds_sent_total", 0)
        ) + stats.reminds_sent
        persona_state["skips_unchanged_total"] = int(
            persona_state.get("skips_unchanged_total", 0)
        ) + stats.remind_skips_unchanged

        had_error = 1 if (stats.cycle_errors > 0 or stats.remind_errors > 0) else 0
        errors_window = list(persona_state.get("errors_last_n_window") or [])
        errors_window.append(had_error)
        if len(errors_window) > 10:
            errors_window = errors_window[-10:]
        persona_state["errors_last_n_window"] = errors_window
        persona_state["errors_last_n"] = sum(errors_window)

        persona_state["last_cycle_at"] = now.isoformat()
        persona_state["last_poll_at"] = now.isoformat()

        self._state_dirty = True

    def _archive_and_reset_on_restart(
        self,
        persona_state: dict[str, Any],
        *,
        prev_pid: int,
        current_pid: int,
        now: datetime,
    ) -> None:
        """A5: waker pid 变化 → 写 .stale.<ts>.json 归档旧计数 + 重置 cycles。

        主 state 文件保持原地（atomic write 契约）；sidecar 只记上次关键计数
        + pid diff 供事后溯源。runtime session 字段（claude_session_id /
        runtime_session_id / runtime_contract_hash）保留——重启不破坏既有
        runtime 状态机连续性。
        """
        state_file = self.config.state_file
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        archive_path = state_file.with_suffix(f".stale.{ts}.json")
        archive_marker = {
            "archived_at": now.isoformat(),
            "previous_pid": prev_pid,
            "new_pid": current_pid,
            "previous_cycles_total": int(persona_state.get("cycles_total", 0)),
            "previous_reminds_sent_total": int(
                persona_state.get("reminds_sent_total", 0)
            ),
            "previous_errors_last_n": int(persona_state.get("errors_last_n", 0)),
        }
        with contextlib.suppress(OSError):
            # 归档失败不阻塞主流程（best-effort）；view 仍能从 cycles=0 看到 restart
            archive_path.write_text(
                json.dumps(archive_marker, ensure_ascii=False, indent=2)
            )

        for key in (
            "cycles_total",
            "reminds_sent_total",
            "skips_unchanged_total",
            "errors_last_n_window",
            "errors_last_n",
            "started_at",
            "last_cycle_at",
            "last_poll_at",
            "busy_started_at",
            "session_busy_since",
            "busy_pid",
        ):
            persona_state.pop(key, None)

    def _check_busy_crash_recovery(self) -> None:
        """I2（A2 + A8 边界）：启动时回收上次 crash 残留 busy 标记。

        state 文件若含 ``busy_pid`` 且进程已死（kill -0 抛 ProcessLookupError）
        → 清零本地 state + server ``agents.last_busy_since``；进程仍活则
        视作并发 waker，不动（让对方的 _clear_busy 自己处理）。
        """
        persona_state = self._persona_state(self.config.persona)
        busy_pid_raw = persona_state.get("busy_pid")
        if busy_pid_raw is None:
            return
        try:
            busy_pid = int(busy_pid_raw)
        except (TypeError, ValueError):
            busy_pid = None
        if busy_pid is not None:
            try:
                os.kill(busy_pid, 0)
                # 进程仍活 → 视为并发 waker，不回收
                typer.echo(
                    f"[simple-waker] busy_pid={busy_pid} still alive; "
                    "skipping crash recovery",
                    err=True,
                )
                return
            except ProcessLookupError:
                pass  # 进程已死 → 回收
            except PermissionError:
                # 别人的 PID（EPERM）→ 不动，由对方负责清零
                typer.echo(
                    f"[simple-waker] busy_pid={busy_pid} not owned; "
                    "skipping crash recovery",
                    err=True,
                )
                return
            except OSError:
                return
        # 进程已死 → 清零本地 + 兜底清 server（best-effort，不抛错）
        for key in ("session_busy_since", "busy_started_at", "busy_pid"):
            persona_state.pop(key, None)
        self._state_dirty = True
        self._save_state_if_needed(force=True)
        # 启动时无 caller context，临时 stats 仅承载计数（_patch_server_busy
        # 当前不读 stats 字段，但保持参数形状一致便于将来加 metric）。
        self._patch_server_busy(SimpleWakerStats(), busy_since=None)
        typer.echo(
            "[simple-waker] recovered from prior crash; busy_since cleared",
            err=True,
        )

    def _patch_server_busy(
        self,
        stats: SimpleWakerStats,
        *,
        busy_since: datetime | None,
    ) -> None:
        """PATCH server ``agents.last_busy_since``（A1）。

        失败兜底（catch WorkerError），不阻塞主流程。心跳信号本身
        是 best-effort 可观测性扩展，server 短暂不可达不应反向
        拖累 remind。
        """
        heartbeat_fn = getattr(self.client, "agent_heartbeat", None)
        if heartbeat_fn is None:
            # 测试 mock 可能未实现此方法；不报错也不计入 stats。
            return
        try:
            heartbeat_fn(busy_since=busy_since)
        except WorkerError as exc:
            typer.echo(f"[simple-waker:heartbeat] {exc}", err=True)

    def _save_state_if_needed(self, *, force: bool = False) -> None:
        if not force and not self._state_dirty:
            return

        save_bridge_state(self.config.state_file, self.state)
        self._state_dirty = False
