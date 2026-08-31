"""Waker state.json schema (实验 d12c328c I5)。

``cli/simple_waker.py`` 写 ``.map/simple-waker-state-{persona}.json`` 时的字段
schema 锚点；视图层 ``cli/waker_status_view.py`` 据此做 fallback 链推导。

字段字典：

- ``pid`` (int, 引入版本 v0.10)：当前 waker 进程 pid；进程重启时由
  ``_archive_and_reset_on_restart`` 归档旧计数。
- ``started_at`` (str ISO 8601, v0.10)：本 waker 进程首次启动时间戳。
- ``last_poll_at`` (str ISO 8601, v0.10)：最近一次 poll cycle 完成时间戳。
- ``busy_started_at`` (str ISO 8601, v0.10)：最近一次进入 busy session
  的时间戳；正常 session 结束由 ``_clear_busy`` 清零。
- ``session_busy_since`` / ``busy_pid`` (str / int, v0.10)：与
  ``busy_started_at`` 同步写入，用于跨进程 stale 检测。
- ``expected_remind_runtime_seconds`` (int, v0.15 d12c328c I1)：
  server 侧 ``Settings.expected_remind_runtime_minutes`` 镜像值（seconds）；
  CLI 视图读此字段推导 busy 容忍。**fallback 链**（缺字段时 CLI 走）：
  state.json > env ``MAP_EXPECTED_REMIND_RUNTIME_MINUTES`` > 30min default
  （不回退到 ``idle_stale_w``，那是 fallback 偷换语义，正是 d12c328c
  修复的根因）。
- ``cycles_total`` / ``reminds_sent_total`` / ``skips_unchanged_total``
  (int, v0.10)：累计计数。
- ``errors_last_n_window`` (list[int], v0.10)：最近 10 cycle 错误窗口；
  ``errors_last_n`` 是其 sum，供视图渲染。
- ``last_cycle_error`` / ``last_cycle_error_at`` / ``last_remind_error``
  / ``last_remind_error_at`` (str, v0.10)：最近错误信息 + 时间戳。
- ``last_remind_at`` / ``last_remind_work_count`` /
  ``last_remind_topic_count`` / ``last_wake_signature``
  (str / int / str, v0.10)：最近一次 remind 的元数据 + 签名去重锚点。
- ``runtime_contract_hash`` / ``runtime_contract_version`` /
  ``runtime_contract_updated_at`` (str, v0.13)：与 runtime SDK contract
  版本绑定，contract 变化触发 session 重置。
- ``claude_session_id`` / ``runtime_session_id`` / ``cursor_agent_id``
  (str, v0.10)：各 runtime 的 session 句柄；resume 用。
- ``runtime_backend`` (str, v0.13)：runtime 类型标识（claude / cursor）。
- ``pid_archived_at`` / ``archived_cycles_total`` 等归档字段（v0.10）：
  见 ``_archive_and_reset_on_restart``，重启前的累积计数快照。

schema_version 字段（顶层，非 persona 内）：固定 1；旧 state 不带字段时
``load_bridge_state`` 会 setdefault 兜底。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WakerStateField:
    """单个字段的 schema 元数据。"""

    name: str
    type_name: str
    fallback: str
    introduced_version: str


# 视图层 fallback 链常量
EXPECTED_REMIND_RUNTIME_FALLBACK_SECONDS = 30 * 60  # 30min default
MAP_EXPECTED_REMIND_RUNTIME_ENV = "MAP_EXPECTED_REMIND_RUNTIME_MINUTES"


WAKER_STATE_SCHEMA: tuple[WakerStateField, ...] = (
    WakerStateField(
        name="pid",
        type_name="int",
        fallback="(required, waker init writes os.getpid())",
        introduced_version="v0.10",
    ),
    WakerStateField(
        name="started_at",
        type_name="str ISO 8601",
        fallback="setdefault on first cycle",
        introduced_version="v0.10",
    ),
    WakerStateField(
        name="last_poll_at",
        type_name="str ISO 8601",
        fallback="(required, per-cycle write)",
        introduced_version="v0.10",
    ),
    WakerStateField(
        name="busy_started_at",
        type_name="str ISO 8601",
        fallback="absent when idle",
        introduced_version="v0.10",
    ),
    WakerStateField(
        name="expected_remind_runtime_seconds",
        type_name="int (seconds)",
        fallback=(
            f"state.json > env {MAP_EXPECTED_REMIND_RUNTIME_ENV} > "
            f"{EXPECTED_REMIND_RUNTIME_FALLBACK_SECONDS}s default "
            f"(NEVER fallback to idle_stale_w)"
        ),
        introduced_version="v0.15 (experiment d12c328c I1)",
    ),
)
