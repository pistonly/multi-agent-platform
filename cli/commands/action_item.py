"""``map topic action-item ...`` sub-app — FS 话题执行项（T33 extraction）.

Moved verbatim from ``cli/commands/topic.py``（plan v3 I4，A4/A5）：
纯本地写——读解析 → 变更 → 原子写回 action-items.yaml → maybe_auto_sync。
owner 按 persona 短名路由到 work 义务（A2），close 门禁校验清零（A3）。

``cli/commands/topic.py`` imports ``action_item_app`` and registers it via
``topic_app.add_typer(action_item_app, name="action-item")``. Command bodies
keep the lazy-import convention (``cli.commands.fs`` helpers, ``map_fs``) to
avoid pulling the whole command tree at module import time.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

# FS 话题执行项（action-items.yaml）——收敛时落盘，close 门禁校验清零
action_item_app = typer.Typer(
    help=(
        "话题执行项(action-items.yaml)命令：add / complete / cancel / list。"
        "收敛时由 host 落盘 open 项，owner 完成/取消后清零，close 门禁校验无 open 才放行。"
    ),
    rich_markup_mode=None,
)


def _ai_local_topic(workspace: Path, root: str, topic: str) -> Any:
    """定位 FS 话题文件夹；缺失时给出可操作错误（写回前必须存在）。"""
    from map_fs import parse_topic_dir

    parsed = parse_topic_dir(workspace / root / "topics" / topic, workspace)
    if parsed is None:
        typer.echo(f"Error: topic not found: {topic}（`map topic action-item` 只作用于 map/ 话题）", err=True)
        raise typer.Exit(1)
    return parsed


def _ai_load(workspace: Path, root: str, topic: str) -> list:
    """读 action-items.yaml；格式错漏直接报错拒绝写（A1 不静默）。"""
    from map_fs import read_action_items

    items, error = read_action_items(workspace, topic, content_root=root)
    if error is not None:
        typer.echo(f"Error: {error}", err=True)
        typer.echo(
            "  修复 action-items.yaml 后再执行（命令见 `map topic action-item --help`）；"
            "格式错漏时 server close 门禁同样会 409 拦截",
            err=True,
        )
        raise typer.Exit(1)
    return items


def _ai_save(workspace: Path, root: str, topic: str, items: list, *, no_sync: bool) -> Path:
    from map_fs import write_action_items

    from cli.fs_projection import maybe_auto_sync

    path = write_action_items(workspace, topic, items, content_root=root)
    maybe_auto_sync(no_sync=no_sync, workspace=workspace)
    return path


@action_item_app.command("list")
def action_item_list(
    topic: str = typer.Option(..., "--topic", help="话题 slug"),
) -> None:
    """列出话题 action-items.yaml 的全部执行项（含解析错误提示）。"""
    from cli.commands.fs import _content_root_name, _workspace

    workspace = _workspace()
    root = _content_root_name(workspace)
    _ai_local_topic(workspace, root, topic)
    items = _ai_load(workspace, root, topic)
    if not items:
        typer.echo(f"(no action items — topic {topic} 尚无 action-items.yaml 执行项)")
        raise typer.Exit(0)
    label = {"open": "open", "done": "done", "cancelled": "cancelled"}
    for item in items:
        detail = ""
        if item.evidence:
            detail = f" evidence={item.evidence!r}"
        elif item.reason:
            detail = f" reason={item.reason!r}"
        typer.echo(f"#{item.id} [{label[item.status]}] {item.title} (owner: {item.owner}{detail})")


@action_item_app.command("complete")
def action_item_complete(
    topic: str = typer.Option(..., "--topic", help="话题 slug"),
    item_id: int = typer.Option(..., "--id", help="执行项编号（见 action-items.yaml / `action-item list`）"),
    evidence: str = typer.Option(
        ..., "--evidence", help="完成证据：commit hash / pytest 摘要 / 文件路径（必填——不许自说自话）"
    ),
    no_sync: bool = typer.Option(False, "--no-sync", help="跳过投影自动同步"),
) -> None:
    """标记执行项完成并写入证据（status: done + evidence）。"""
    if not evidence.strip():
        typer.echo("Error: --evidence 必填（commit hash / pytest 摘要 / 文件路径），空证据拒绝", err=True)
        raise typer.Exit(2)
    _ai_mutate(topic, item_id, status="done", evidence=evidence.strip(), no_sync=no_sync)


@action_item_app.command("cancel")
def action_item_cancel(
    topic: str = typer.Option(..., "--topic", help="话题 slug"),
    item_id: int = typer.Option(..., "--id", help="执行项编号（见 action-items.yaml / `action-item list`）"),
    reason: str = typer.Option(..., "--reason", help="放弃理由（必填，审计留痕；cancel 不挡 close）"),
    no_sync: bool = typer.Option(False, "--no-sync", help="跳过投影自动同步"),
) -> None:
    """标记执行项取消（status: cancelled + reason，显式放弃）。"""
    if not reason.strip():
        typer.echo("Error: --reason 必填（放弃理由，审计留痕）", err=True)
        raise typer.Exit(2)
    _ai_mutate(topic, item_id, status="cancelled", reason=reason.strip(), no_sync=no_sync)


@action_item_app.command("add")
def action_item_add(
    topic: str = typer.Option(..., "--topic", help="话题 slug"),
    owner: str = typer.Option(..., "--owner", help="owner persona 短名（host / participant / reviewer）"),
    title: str = typer.Option(..., "--title", help="执行项标题"),
    no_sync: bool = typer.Option(False, "--no-sync", help="跳过投影自动同步"),
) -> None:
    """收敛时追加一条 open 执行项（新 id = 当前最大 +1）。"""
    if not owner.strip() or not title.strip():
        typer.echo("Error: --owner 与 --title 均必填", err=True)
        raise typer.Exit(2)

    from datetime import datetime, timezone

    from map_fs import FsActionItem

    from cli.commands.fs import _content_root_name, _workspace

    workspace = _workspace()
    root = _content_root_name(workspace)
    parsed = _ai_local_topic(workspace, root, topic)
    # A7（50cddb7e）：closed=零尾款 invariant——closed 话题不应再写入 open 执行项，
    # 否则 close 门禁保证的「零 open」被破（管道审计实证：4 个 closed 话题被写入 open 项）。
    if parsed.status == "closed":
        typer.echo(
            f"Error: 话题 {topic} 已 closed——closed 话题不能再写入 open 执行项"
            "（closed=零尾款 invariant）",
            err=True,
        )
        typer.echo(
            "  后续事项请用 topic comment 记录；确需执行项的场合先处理话题的 close 态",
            err=True,
        )
        raise typer.Exit(1)
    items = _ai_load(workspace, root, topic)
    new_id = max((item.id for item in items), default=0) + 1
    items.append(
        FsActionItem(
            id=new_id,
            title=title.strip(),
            owner=owner.strip(),
            status="open",
            created_at=datetime.now(timezone.utc),
        )
    )
    path = _ai_save(workspace, root, topic, items, no_sync=no_sync)
    typer.echo(f"Wrote {path} — 新增 #{new_id} [{owner.strip()}] {title.strip()}")


def _ai_mutate(
    topic: str,
    item_id: int,
    *,
    status: str,
    evidence: str = "",
    reason: str = "",
    no_sync: bool = False,
) -> None:
    """complete/cancel 公共路径：只允许 open → done/cancelled，写回 yaml。"""
    from dataclasses import replace

    from cli.commands.fs import _content_root_name, _workspace

    workspace = _workspace()
    root = _content_root_name(workspace)
    _ai_local_topic(workspace, root, topic)
    items = _ai_load(workspace, root, topic)
    target = next((item for item in items if item.id == item_id), None)
    if target is None:
        typer.echo(f"Error: action item #{item_id} 不存在（topic: {topic}）", err=True)
        raise typer.Exit(1)
    if target.status != "open":
        typer.echo(
            f"Error: #{item_id} 已是 {target.status}，不能再次标记（open → done/cancelled 单向）",
            err=True,
        )
        raise typer.Exit(1)
    updated = [
        replace(target, status=status, evidence=evidence, reason=reason) if item.id == item_id else item
        for item in items
    ]
    path = _ai_save(workspace, root, topic, updated, no_sync=no_sync)
    if evidence:
        typer.echo(f"Wrote {path} — #{item_id} → done（evidence: {evidence}）")
    else:
        typer.echo(f"Wrote {path} — #{item_id} → cancelled（reason: {reason}）")
