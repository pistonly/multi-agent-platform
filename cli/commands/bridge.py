"""交互会话桥接命令（实验 db97aeac I2）。

``map bridge hook``：Claude Code Stop hook 的入口。读取 ``map work``
平台事实，经 ``cli.interactive_bridge`` 纯逻辑层做去重 / 有限升级判定，
按 hook 契约输出提醒；桥接层只读平台事实，不写 MAP、不做业务判断。

失败语义：任何异常（API 不可达、配置缺失、state 损坏）都静默 exit 0
——hook 永远不得阻断用户回合；调试信息仅在 ``MAP_BRIDGE_DEBUG=1`` 时
写 stderr。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import typer

from cli import runner
from cli.interactive_bridge import (
    _OBLIGATION_TODO_BUCKETS,
    DEFAULT_MAX_REMIND_COUNT,
    DEFAULT_MIN_REMIND_SECONDS,
    bridge_state_path,
    evaluate_remind,
    has_obligation_work,
    load_state,
    record_remind,
    record_trigger,
    save_state,
    work_fingerprint,
)
from cli.project_context import current_context

bridge_app = typer.Typer(
    help="Interactive-session bridge: pull `map work` obligations into the live session (Stop hook entry).",
)


def _fetch_work() -> dict[str, Any]:
    """取 wakeable 视角的 work 快照（测试注入面）。"""
    with runner._client_ctx() as client:
        return client.get_agent_work(
            notification_limit=50, notification_category="wakeable"
        ).model_dump(mode="python")


def _obligation_lines(work: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    progress = work.get("topic_progress") or {}
    for item in progress.get("items") or []:
        if not isinstance(item, dict):
            continue
        kinds = sorted(
            {
                str(wi.get("kind"))
                for wi in item.get("work_items") or []
                if isinstance(wi, dict) and wi.get("priority") == "obligation"
            }
        )
        if kinds:
            lines.append(
                f"- 话题「{item.get('topic_title')}」（{item.get('discussion_round')}）：{', '.join(kinds)}"
            )
    todos = work.get("todos") or {}
    for bucket in _OBLIGATION_TODO_BUCKETS:
        entries = todos.get(bucket) or []
        if entries:
            lines.append(f"- {bucket}：{len(entries)} 项")
    unread = (work.get("notifications") or {}).get("unread_count") or 0
    if unread:
        lines.append(f"- 未读通知：{unread} 条")
    return lines


def _build_remind_text(work: dict[str, Any], *, escalation: int) -> str:
    header = "[MAP] 平台有待办需要处理。"
    if escalation >= 2:
        header = f"[MAP] 平台待办仍未处理（第 {escalation} 次提醒）。"
    lines = _obligation_lines(work)
    protocol = (
        "处理协议：读 .cursor/skills/map-project-collab/references/wake.md "
        "→ 按 persona Skill 行动 → 用 map --persona <name> 写回 MAP。"
    )
    return "\n".join([header, *lines, protocol])


@bridge_app.command("hook")
def bridge_hook(
    min_remind_seconds: float = typer.Option(
        DEFAULT_MIN_REMIND_SECONDS, "--min-remind-seconds", help="同一指纹重复提醒的最小间隔秒数。"
    ),
    max_remind_count: int = typer.Option(
        DEFAULT_MAX_REMIND_COUNT, "--max-remind-count", help="同一指纹最多提醒次数，之后沉默。"
    ),
    runtime: str = typer.Option("claude-code", "--runtime", help="写入 state 的 runtime 标识。"),
) -> None:
    """Stop hook 入口：首次发现待办 block 回合并注入摘要；重复提醒非阻塞。"""
    try:
        _run_hook(
            min_remind_seconds=min_remind_seconds,
            max_remind_count=max_remind_count,
            runtime=runtime,
        )
    except Exception as exc:  # noqa: BLE001 — hook 永远不得阻断用户回合
        if os.environ.get("MAP_BRIDGE_DEBUG"):
            typer.echo(f"[bridge:debug] {type(exc).__name__}: {exc}", err=True)


def _run_hook(*, min_remind_seconds: float, max_remind_count: int, runtime: str) -> None:
    from cli import main as _main  # runtime state (injection surface)

    context = current_context()
    persona = _main._cli_options.get("persona") or context.config.default_persona
    work = _fetch_work()

    state_path = bridge_state_path(context.workspace_root, persona)
    state = load_state(state_path)
    now = datetime.now(timezone.utc)
    record_trigger(state, now=now, runtime=runtime)

    if has_obligation_work(work):
        fingerprint = work_fingerprint(work)
        decision = evaluate_remind(
            state,
            fingerprint=fingerprint,
            now=now,
            min_remind_seconds=min_remind_seconds,
            max_remind_count=max_remind_count,
        )
        if decision["remind"]:
            record_remind(state, fingerprint=fingerprint, now=now, decision=decision)
            text = _build_remind_text(work, escalation=decision["escalation"])
            if decision["block"]:
                # Claude Code Stop hook 契约：decision=block 把 reason 回灌给模型。
                typer.echo(json.dumps({"decision": "block", "reason": text}, ensure_ascii=False))
            else:
                typer.echo(text, err=True)
    save_state(state_path, state)
