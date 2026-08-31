"""``map waker ...`` sub-app — waker 运维视图命令（实验 waker-status-view I3）。

视图只读（A4）：不写日志 / 不发通知 / 不重启 waker / 不改 state。
与 T1-T4 单向流一致（视图是末梢，不闭环回去）。
"""
from __future__ import annotations

import json as _json
from datetime import datetime, timezone
from pathlib import Path

import typer

waker_app = typer.Typer(help="Waker 运维视图（实验 waker-status-view；只读）")


def _resolve_state_dir(project_root: Path | None) -> Path:
    """Resolve ``.map/`` directory under project_root (or cwd)。"""
    root = project_root or Path.cwd()
    return root / ".map"


@waker_app.callback()
def waker_callback() -> None:
    """Waker 运维视图命令集（只读）。

    强制多命令模式（与 cli/commands/host.py 同款约定）：必须
    ``map waker status`` 才会执行子命令。
    """
    return


@waker_app.command("status")
def waker_status(
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="项目根目录（含 .map/）。默认 cwd。",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="JSON 输出（结构化行 + state 判定阈值；便于脚本消费）。",
    ),
    stale_threshold_seconds: int = typer.Option(
        300,
        "--stale-threshold-seconds",
        help="dead 阈值（秒）。默认 300。",
    ),
) -> None:
    """Render waker 状态视图：persona × 10 字段最小集（live/stale/dead）。

    数据源：
      - .map/simple-waker-state-{persona}.json（waker 自写；实验 I2）
      - 每行派生 state（A2 三档 + busy 卡死升级）
    """
    from cli.waker_status_view import (
        collect_waker_status,
        render_waker_status_table,
    )

    state_dir = _resolve_state_dir(project_root)
    now = datetime.now(timezone.utc)
    rows = collect_waker_status(state_dir, now=now)

    if as_json:
        # 去掉内部字段（_state_file）
        clean = [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows]
        payload = {
            "generated_at": now.isoformat(),
            "stale_threshold_seconds": stale_threshold_seconds,
            "rows": clean,
        }
        typer.echo(_json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return

    typer.echo(render_waker_status_table(rows))
