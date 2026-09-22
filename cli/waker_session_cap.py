"""simple-waker runtime session 硬上限（实验 bccb59ea A4）。

同一 runtime session 连续成功唤醒达到阈值即强制 ``reset_session()``，
下轮唤醒开新会话。动机：长会话上下文无限膨胀；cache 未生效端点上每轮
全价重发，即使 cache 生效，超长上下文也抬高基线成本并稀释注意力。

precedence：CLI ``--session-max-wakes`` > env ``MAP_WAKER_SESSION_MAX_WAKES``
> 默认 ``SESSION_MAX_WAKES_DEFAULT``（300）。显式 ``<=0`` 关闭硬上限。
仅在「有旧会话可复用」时判断——无 ``claude_session_id`` 时唤醒本身就是
新会话，直接返回（话题切换重置刚清掉 session id 时也自然跳过）。

从 cli/simple_waker.py 拆出（800 行上限守卫 test_module_size_caps.py）。
"""
from __future__ import annotations

import os
from typing import Any

import typer

SESSION_MAX_WAKES_DEFAULT = 300
SESSION_MAX_WAKES_ENV = "MAP_WAKER_SESSION_MAX_WAKES"


def resolve_session_max_wakes(cli_value: int | None = None) -> int | None:
    """Resolve session wake cap. Precedence: CLI flag > env > ``None``（用默认）.

    返回 ``None`` 表示未显式配置，由调用方取 ``SESSION_MAX_WAKES_DEFAULT``；
    显式 ``<=0`` 表示关闭硬上限（返回解析后的原值，调用方按 ``<=0`` 判断）。
    """
    if cli_value is not None:
        return cli_value
    raw = (os.environ.get(SESSION_MAX_WAKES_ENV) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def reset_session_if_wake_limit_reached(
    *,
    backend: Any,
    persona_state: dict[str, Any],
    stats: Any,
    persona: str,
    cli_override: int | None,
) -> None:
    """达阈值先 ``reset_session()`` 再唤醒；未达 / 已关闭 / 无旧会话则跳过。

    ``stats.session_resets_wake_limit`` 在实际重置时置 1（SimpleWakerStats
    聚合字段，add() 会累计）。``persona_state["session_wake_count"]`` 与
    session 生命周期绑定，跨 waker 进程重启保留（重启不换 session）。
    """
    if not persona_state.get("claude_session_id"):
        return
    resolved = resolve_session_max_wakes(cli_override)
    max_wakes = SESSION_MAX_WAKES_DEFAULT if resolved is None else resolved
    if max_wakes <= 0:
        return  # 显式关闭
    count = int(persona_state.get("session_wake_count", 0))
    if count < max_wakes:
        return
    typer.echo(
        f"[simple-waker] session wake cap reached for {persona} "
        f"({count}/{max_wakes}); resetting runtime session",
        err=True,
    )
    await backend.reset_session()
    persona_state["session_wake_count"] = 0
    stats.session_resets_wake_limit = 1
