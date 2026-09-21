"""``map work`` — 统一工作快照命令（自 cli/main.py 拆出，保持 main.py
800 行上限，map exp 4e4206de I1）。

命令本体 + ``--kinds`` registry 输出。默认输出为精简人类视图
（:mod:`cli.work_compact_view`）；``--verbose`` / 显式 ``--format yaml``
恢复完整诊断视图；``--format json`` 机器契约不变。
"""

from __future__ import annotations

import typer
from map_client.client import MAPClient

from cli.runner import _run
from cli.waker_heartbeat_render import render_waker_heartbeat_banner


def _print_work_kinds(explain: str | None, fmt: str = "list") -> None:
    """``map work --kinds`` / ``--explain <kind>``：输出 server 侧 KINDS registry。

    实验 d559f431（work-kind-dispatch-single-source）A2 方向 A 过渡：wake.md
    分发表以此输出为静态引用基准，CI 逐行一致校验（A3）消费同一 registry。
    ``fmt="md"`` 输出 ``render_kinds_md()`` 渲染形态（与 wake.md 标记块
    逐字符一致的对照基准）。
    """
    # 延迟 import：避免 CLI 启动路径拖入 server 依赖（先例 simple_waker 的 server_config）
    from server.services.work_kinds import (
        WORK_ITEM_KINDS,
        get_kind_spec,
        render_kinds_md,
    )

    if fmt == "md" and explain is None:
        typer.echo(render_kinds_md())
        return

    if explain is not None:
        spec = get_kind_spec(explain)
        if spec is None:
            typer.echo(
                f"未登记的 kind: {explain}（registry 漂移信号，见 server/services/work_kinds.py）",
                err=True,
            )
            raise typer.Exit(code=2)
        specs = (spec,)
    else:
        specs = WORK_ITEM_KINDS

    for spec in specs:
        typer.echo(f"kind: {spec.kind}")
        typer.echo(f"  clear_action: {spec.clear_action}")
        typer.echo(f"  skill: {spec.skill}")
        if spec.note:
            typer.echo(f"  note: {spec.note}")
        typer.echo()


def map_work(
    kinds: bool = typer.Option(
        False,
        "--kinds",
        help="输出 work item kind 分发 registry（清理动作+归属 Skill+说明；server 侧单一真相，实验 d559f431 方向 A）",
    ),
    explain: str | None = typer.Option(
        None,
        "--explain",
        help="输出单个 kind 的分发规格（如 --explain mentions；未登记 kind 退出码 2）",
    ),
    kinds_format: str = typer.Option(
        "list",
        "--kinds-format",
        help="kinds 输出形态：list（逐条人类可读）或 md（wake.md 标记块对照基准，实验 d559f431 A3）",
    ),
    notification_limit: int = typer.Option(50, "--notification-limit", min=1, max=200),
    notification_category: str = typer.Option(
        "all",
        "--notification-category",
        help="all (human default), wakeable (waker view), or digest",
    ),
    client: str | None = typer.Option(None, "--client", help="'waker' 标记为 simple-waker 轮询（一并刷 last_waker_poll_at）"),
    summary: bool = typer.Option(
        False,
        "--summary",
        help="Return the compact 6-bucket by_kind summary instead of the full work snapshot.",
    ),
    include_all_personas: bool = typer.Option(
        False,
        "--include-all-personas",
        help="Include host-only buckets (explicit_only, informational_only, action_items) when the current persona would otherwise hide them.",
    ),
    topics_limit: int = typer.Option(
        10,
        "--summary-topics-limit",
        min=1,
        max=100,
        help="Max topics per summary bucket (default 10).",
    ),
    experiments_limit: int = typer.Option(
        5,
        "--summary-experiments-limit",
        min=1,
        max=50,
        help="Max experiments per summary bucket (default 5).",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="完整诊断视图（全字段 YAML，含 lock/source/timestamps）；默认为精简视图（map exp 4e4206de I1）",
    ),
) -> None:
    """Unified work snapshot: whoami + topic-progress + todos + unread notifications.

    CLI defaults to ``all`` so humans see the same unread count as
    ``notification list --unread-only``. Wakers should pass
    ``--notification-category wakeable`` explicitly.

    Pass ``--summary`` to get the compact 6-bucket by_kind summary for the
    /work top card; combine with ``--include-all-personas`` for full
    visibility.

    默认输出为精简人类视图（空分区折叠、通知单行摘要）；``--verbose`` 或显式
    ``--format yaml`` 恢复完整诊断视图，``--format json`` 机器契约不变。
    """
    if kinds or explain is not None:
        _print_work_kinds(explain, fmt=kinds_format)
        return

    if summary:

        def action(c: MAPClient):
            return c.get_agent_work_summary(
                include_all_personas=include_all_personas,
                topics_limit=topics_limit,
                experiments_limit=experiments_limit,
            )

        _run(action, detect_deprecated=True)
        return

    def action(c: MAPClient):
        render_waker_heartbeat_banner(c)
        return c.get_agent_work(
            notification_limit=notification_limit,
            notification_category=notification_category,
            client=client,
        )

    if verbose:
        _run(action, detect_deprecated=True)
        return

    from cli.work_compact_view import render_work_compact

    _run(action, detect_deprecated=True, human_renderer=render_work_compact)
