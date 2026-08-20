"""``map fs ...`` sub-app — map/ 文件夹事实源操作。

两类命令：

* **纯文件操作**（零 API、零网络）：``init`` / ``topic create`` / ``comment`` /
  ``list`` / ``show`` / ``work`` / ``migrate-from-docs``。Agent 发言 = 写一个
  ``round<N>-<persona>.md``，学习成本为零，这些命令只是命名约定的便捷封装。
* **验证型写**（走 API）：``advance-round`` / ``close``。服务端校验权限与
  ack 完整性后写回 index.md。

内容根目录：``.map/config.yaml`` 的 ``content_root``（默认 ``map``），
即 ``<workspace>/map/``。
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import typer
import yaml
from map_client.client import MAPClient

from cli.table_render import render_table, truncate

fs_app = typer.Typer(help="map/ folder source-of-truth commands", rich_markup_mode=None)

_ROUND_FILE_RE = re.compile(r"^round(\d+)-([A-Za-z0-9_.\-]+)\.md$")


def _workspace() -> Path:
    """定位 workspace（.map/ 的父目录），失败则提示先跑 map bootstrap。"""
    from map_client.project_config import find_map_dir

    map_dir = find_map_dir(None)
    if map_dir is None:
        typer.echo("Error: .map/config.yaml not found. Run `map bootstrap` first.", err=True)
        raise typer.Exit(1)
    return map_dir.parent


def _content_root_name(workspace: Path) -> str:
    map_cfg = workspace / ".map" / "config.yaml"
    if map_cfg.is_file():
        try:
            data = yaml.safe_load(map_cfg.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            data = {}
        if isinstance(data, dict) and data.get("content_root"):
            return str(data["content_root"])
    return "map"


def _default_persona(workspace: Path) -> str:
    map_cfg = workspace / ".map" / "config.yaml"
    if map_cfg.is_file():
        try:
            data = yaml.safe_load(map_cfg.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            data = {}
        if isinstance(data, dict) and data.get("default_persona"):
            return str(data["default_persona"])
    return "host"


def _persona(persona: str | None) -> str:
    """解析 persona：子命令显式 --persona > 全局 map --persona > config 默认。

    全局 ``map --persona <name> fs ...`` 与其他命令组语义一致，Agent 无需
    记住 fs 子命令要重复传 --persona。
    """
    if persona:
        return persona
    try:
        from cli.main import _cli_options  # lazy import，避免循环依赖

        global_persona = _cli_options.get("persona")
    except ImportError:
        global_persona = None
    if global_persona:
        return str(global_persona)
    return _default_persona(_workspace())


def _current_round(workspace: Path, slug: str) -> int:
    from map_fs import parse_topic_dir

    topic = parse_topic_dir(workspace / _content_root_name(workspace) / "topics" / slug, workspace)
    if topic is None:
        typer.echo(f"Error: fs topic not found: {slug} (run `map fs topic create` first)", err=True)
        raise typer.Exit(1)
    return topic.round_number


# ---------------------------------------------------------------------------
# 纯文件操作（离线）
# ---------------------------------------------------------------------------


@fs_app.command("init")
def fs_init() -> None:
    """创建内容根目录结构：<content_root>/topics 与 <content_root>/experiments。"""
    from map_fs import DEFAULT_CONTENT_ROOT

    workspace = _workspace()
    root = workspace / _content_root_name(workspace)
    for sub in ("topics", "experiments"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    typer.echo(
        f"Initialized {root / 'topics'} and {root / 'experiments'} (content_root={root.name or DEFAULT_CONTENT_ROOT})"
    )


def write_new_fs_topic(
    *,
    title: str,
    slug: str | None,
    description: str = "",
    participants: str | None = None,
    creator: str | None = None,
) -> Path:
    """离线创建话题文件夹 + index.md。``map topic create`` 与 ``map fs topic-create`` 共用。

    slug 为空时由 title 生成（两个入口行为一致）。title 为空直接报错退出——
    部分 typer/click 版本组合不强制校验必填 CLI 选项，缺失的 ``--title``
    会以 None 穿透到函数体（回归见 tests/test_topic_routing.py）。
    """
    from map_fs import slugify, write_topic_index

    resolved_title = (title or "").strip()
    if not resolved_title:
        typer.echo("Error: --title is required.", err=True)
        raise typer.Exit(1)
    workspace = _workspace()
    declared = [p.strip() for p in (participants or "").split(",") if p.strip()] or None
    return write_topic_index(
        workspace,
        (slug or "").strip() or slugify(resolved_title),
        title=resolved_title,
        creator=_persona(creator),
        description=description,
        participants=declared,
        content_root=_content_root_name(workspace),
    )


@fs_app.command("topic-create")
def fs_topic_create(
    title: str = typer.Option(..., "--title"),
    slug: str | None = typer.Option(
        None, "--slug", help="文件夹名，如 fs-source-of-truth；缺省由 --title 生成"
    ),
    creator: str | None = typer.Option(None, "--creator", help="默认取 .map/config.yaml 的 default_persona"),
    description: str = typer.Option("", "--description"),
    participants: str | None = typer.Option(
        None, "--participants", help="参与人白名单（逗号分隔，如 host,participant）；仅白名单内 persona 收到 FS 待办"
    ),
) -> None:
    """离线创建话题文件夹 + index.md（不调 API）。高级入口；日常用 ``map topic create``。"""
    index = write_new_fs_topic(
        title=title,
        slug=slug,
        description=description,
        participants=participants,
        creator=creator,
    )
    typer.echo(f"Created {index}")


@fs_app.command("comment")
def fs_comment(
    topic: str = typer.Option(..., "--topic", help="话题 slug"),
    body: str | None = typer.Option(None, "--body", help="评论正文（与 --file 二选一）"),
    file: Path | None = typer.Option(None, "--file", help="从 MD 文件读正文"),
    persona: str | None = typer.Option(None, "--persona"),
    round_number: int | None = typer.Option(None, "--round", help="默认取话题当前轮次"),
    round_summary: bool = typer.Option(False, "--round-summary"),
    force: bool = typer.Option(False, "--force", help="覆盖已有评论文件（破坏 immutable 约定）"),
) -> None:
    """离线写一条评论：map/topics/<slug>/round<N>-<persona>.md（不调 API）。"""
    from map_fs import write_round_comment

    workspace = _workspace()
    if (body is None) == (file is None):
        typer.echo("Error: exactly one of --body / --file is required", err=True)
        raise typer.Exit(2)
    text = body if body is not None else file.read_text(encoding="utf-8")
    current = round_number if round_number is not None else _current_round(workspace, topic)
    if force:
        typer.echo(
            "Warning: --force overwrites an existing comment file (breaks the "
            "immutable convention). Commit first if you need the old content auditable.",
            err=True,
        )
    try:
        path = write_round_comment(
            workspace,
            topic,
            round_number=current,
            persona=_persona(persona),
            body=text,
            is_round_summary=round_summary,
            content_root=_content_root_name(workspace),
            overwrite=force,
        )
    except FileExistsError as err:
        typer.echo(f"Error: {err} (use --force to overwrite)", err=True)
        raise typer.Exit(1) from err
    typer.echo(f"Wrote {path}")


@fs_app.command("list")
def fs_list(status: str | None = typer.Option(None, "--status", help="open | closed")) -> None:
    """离线列出所有文件夹话题（实时解析，无网络）。"""
    from map_fs import scan_plane

    workspace = _workspace()
    plane = scan_plane(workspace, _content_root_name(workspace))
    topics = plane.topics
    if status:
        topics = [t for t in topics if t.status == status]
    headers = ["Slug", "Title", "Status", "Round", "Comments", "Creator"]
    rows = [
        [
            t.slug,
            truncate(t.title, 46),
            t.status,
            t.round,
            str(len(t.comments)),
            truncate(t.creator, 24),
        ]
        for t in topics
    ]
    typer.echo(render_table(headers, rows) if rows else "(no fs topics)")


@fs_app.command("show")
def fs_show(
    topic: str = typer.Option(..., "--topic"),
    full: bool = typer.Option(False, "--full", help="打印评论完整正文"),
) -> None:
    """离线查看话题详情（index.md + 评论文件解析结果）。"""
    from map_fs import parse_topic_dir

    workspace = _workspace()
    t = parse_topic_dir(workspace / _content_root_name(workspace) / "topics" / topic, workspace)
    if t is None:
        typer.echo(f"Error: fs topic not found: {topic}", err=True)
        raise typer.Exit(1)
    typer.echo(f"# {t.title}  [{t.slug}]")
    typer.echo(f"status={t.status} round={t.round} creator={t.creator} dir={t.dir_path}")
    typer.echo(f"participants: {', '.join(t.participants)}")
    if not t.comments:
        typer.echo("(no comments)")
        return
    headers = ["Round", "Author", "Excerpt", "File"]
    rows = [[str(c.round), c.author, truncate(c.excerpt, 44), c.file_path] for c in t.comments]
    typer.echo(render_table(headers, rows))
    if full:
        for c in t.comments:
            typer.echo(f"\n===== {c.file_path} =====\n{c.content}")


@fs_app.command("work")
def fs_work(persona: str | None = typer.Option(None, "--persona")) -> None:
    """离线推导 persona 待办（纯函数：文件存在性 → pending_topic_reply 等）。"""
    from map_fs import derive_work, scan_plane

    workspace = _workspace()
    who = _persona(persona)
    plane = scan_plane(workspace, _content_root_name(workspace))
    items = [i for t in plane.topics for i in derive_work(t, who)]
    if not items:
        typer.echo(f"(no fs work for {who})")
        return
    headers = ["Kind", "Topic", "Round", "Detail"]
    rows = [[i.kind, i.topic_slug, str(i.round), i.detail] for i in items]
    typer.echo(render_table(headers, rows))


# ---------------------------------------------------------------------------
# 验证型写（走 API，服务端校验后写回 index.md）
# ---------------------------------------------------------------------------


@fs_app.command("advance-round")
def fs_advance_round(
    topic: str = typer.Option(..., "--topic"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    waive_ack: bool = typer.Option(False, "--waive-ack"),
    waive_reason: str | None = typer.Option(None, "--waive-reason"),
    mark_ready: bool = typer.Option(False, "--mark-ready"),
) -> None:
    """推进轮次（服务端校验 ack 满员后写回 index.md）。"""
    from map_types.schemas.fs import FsAdvanceRoundRequest

    from cli.main import _resolve_project, _run

    payload = FsAdvanceRoundRequest(waive_ack=waive_ack, waive_reason=waive_reason, mark_ready=mark_ready)

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        return c.fs_advance_round(pid, topic, payload)

    _run(action)


@fs_app.command("close")
def fs_close(
    topic: str = typer.Option(..., "--topic"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    reason: str | None = typer.Option(None, "--reason"),
    note: str | None = typer.Option(None, "--note"),
) -> None:
    """关闭话题（服务端校验后写回 index.md status=closed）。"""
    from map_types.schemas.fs import FsCloseRequest

    from cli.main import _resolve_project, _run

    payload = FsCloseRequest(close_reason=reason, close_note=note)

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        return c.fs_close_topic(pid, topic, payload)

    _run(action)


# ---------------------------------------------------------------------------
# 存量迁移
# ---------------------------------------------------------------------------


def _topic_index_meta(topic_dir: Path) -> tuple[str, int, str]:
    """从已迁移的话题文件推导 index.md 元数据：title / round / creator。"""
    max_round = 1
    creator = ""
    for entry in sorted(topic_dir.iterdir()):
        match = _ROUND_FILE_RE.match(entry.name)
        if match:
            max_round = max(max_round, int(match.group(1)))
            if not creator:
                creator = match.group(2)
    return topic_dir.name.replace("-", " ").title(), max_round, creator or "host"


@fs_app.command("migrate-from-docs")
def fs_migrate_from_docs(
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """存量迁移：docs/{topics,experiments,map-history} → map/ 下的事实源布局。"""
    import shutil

    from map_fs import write_topic_index

    workspace = _workspace()
    root = workspace / _content_root_name(workspace)
    docs = workspace / "docs"
    actions: list[str] = []

    # 1. docs/topics/<slug>/ → map/topics/<slug>/（补生成 index.md）
    src_topics = docs / "topics"
    if src_topics.is_dir():
        for entry in sorted(src_topics.iterdir()):
            if not entry.is_dir():
                continue
            dst = root / "topics" / entry.name
            if dst.exists():
                actions.append(f"skip (exists): {dst.relative_to(workspace)}")
                continue
            title, max_round, creator = _topic_index_meta(entry)
            actions.append(f"move dir: docs/topics/{entry.name} -> {dst.relative_to(workspace)}")
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(entry), str(dst))
                write_topic_index(
                    workspace,
                    entry.name,
                    title=title,
                    creator=creator,
                    status="open",
                    round_=max_round,
                    content_root=_content_root_name(workspace),
                )

    # 2. docs/experiments/<name>-{plan,log,review}.{md,yaml} → map/experiments/<name>/
    src_exps = docs / "experiments"
    if src_exps.is_dir():
        for entry in sorted(src_exps.iterdir()):
            match = re.match(r"^(.+)-(plan|log|review)\.(md|yaml)$", entry.name)
            if match is None:
                actions.append(f"skip (unknown pattern): docs/experiments/{entry.name}")
                continue
            name = match.group(1)
            dst_dir = root / "experiments" / name
            dst = dst_dir / entry.name.removeprefix(f"{name}-")
            if dst.exists():
                actions.append(f"skip (exists): {dst.relative_to(workspace)}")
                continue
            actions.append(f"move file: docs/experiments/{entry.name} -> {dst.relative_to(workspace)}")
            if not dry_run:
                dst_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(entry), str(dst))
                index = dst_dir / "index.md"
                if not index.exists():
                    index.write_text(
                        f"---\ntitle: {name.replace('-', ' ').title()}\nphase: done\ncreator: host\n---\n",
                        encoding="utf-8",
                    )

    # 3. docs/map-history/ → map/archive/（原样归档，不解析）
    history = docs / "map-history"
    if history.is_dir():
        dst = root / "archive"
        if dst.exists():
            actions.append(f"skip (exists): {dst.relative_to(workspace)}")
        else:
            actions.append(f"move dir: docs/map-history -> {dst.relative_to(workspace)}")
            if not dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(history), str(dst))

    if not actions:
        typer.echo("Nothing to migrate (docs/ has no topics/experiments/map-history).")
        return
    prefix = "[dry-run] " if dry_run else ""
    for line in actions:
        typer.echo(prefix + line)
