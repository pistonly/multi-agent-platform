"""``map fs ...`` sub-app — map/ 文件夹事实源操作。

两类命令：

* **纯文件操作**（零 API、零网络）：``init`` / ``topic create`` / ``comment`` /
  ``list`` / ``show`` / ``work`` / ``migrate-from-docs`` / ``archive`` /
  ``archive-index``。Agent 发言 = 写一个 ``round<N>-<persona>.md``，学习成本为
  零，这些命令只是命名约定的便捷封装。
* **验证型写**（走 API）：``advance-round`` / ``close``。服务端校验权限与
  ack 完整性后写回 index.md。

内容根目录：``.map/config.yaml`` 的 ``content_root``（默认 ``map``），
即 ``<workspace>/map/``。
"""

from __future__ import annotations

import ast
import json
import re
import uuid
from pathlib import Path
from typing import Any

import typer
import yaml
from map_client.client import MAPClient
from map_client.exceptions import MAPHTTPError

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
    no_sync: bool = typer.Option(False, "--no-sync", help="Skip remote projection sync after the local write"),
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
    from cli.fs_projection import maybe_auto_sync

    maybe_auto_sync(no_sync=no_sync, workspace=_workspace())


@fs_app.command("comment")
def fs_comment(
    topic: str = typer.Option(..., "--topic", "--id", help="话题 slug（--id 为别名，T2-P2）"),
    body: str | None = typer.Option(None, "--body", help="评论正文（与 --file 二选一）"),
    file: Path | None = typer.Option(None, "--file", help="从 MD 文件读正文"),
    persona: str | None = typer.Option(None, "--persona"),
    round_number: int | None = typer.Option(None, "--round", help="默认取话题当前轮次"),
    round_summary: bool = typer.Option(False, "--round-summary"),
    force: bool = typer.Option(False, "--force", help="覆盖已有评论文件（破坏 immutable 约定）"),
    no_sync: bool = typer.Option(False, "--no-sync", help="Skip remote projection sync after the local write"),
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
    from cli.fs_projection import maybe_auto_sync

    maybe_auto_sync(no_sync=no_sync, workspace=workspace)


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
    topic: str = typer.Option(..., "--topic", "--id"),
    full: bool = typer.Option(False, "--full", help="打印评论完整正文"),
) -> None:
    """离线查看话题详情（index.md + 评论文件解析结果）。"""
    from map_fs import parse_topic_dir

    workspace = _workspace()
    root = _content_root_name(workspace)
    t = parse_topic_dir(workspace / root / "topics" / topic, workspace)
    if t is None:
        # v0.14 读路径：归档目录命中时给「已归档」指引而非裸 404（不泄露 ghost）
        if (workspace / root / "archive" / "topics" / topic).is_dir():
            typer.echo(
                f"Error: topic '{topic}' is archived (map/archive/topics/{topic}/) — "
                f"restore via `map fs archive --topic {topic} --undo` to resume",
                err=True,
            )
            raise typer.Exit(1)
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
# 部署矩阵握手 + 验证型写（validate → 本地写回 → commit）
# ---------------------------------------------------------------------------


def fs_topic_to_detail_read(topic: Any) -> Any:
    """parser FsTopic → FsTopicDetailRead（evidence / push 投影共用）。"""
    from map_types.schemas.fs import FsActionItemRead, FsCommentRead, FsTopicDetailRead

    return FsTopicDetailRead(
        id=topic.id,
        slug=topic.slug,
        title=topic.title,
        description=topic.description,
        status=topic.status,
        discussion_round=topic.round,
        creator=topic.creator,
        comment_count=len(topic.comments),
        participants=topic.participants,
        declared_participants=topic.declared_participants,
        created_at=topic.created_at,
        updated_at=topic.updated_at,
        dir_path=topic.dir_path,
        comments=[
            FsCommentRead(
                id=c.id,
                topic_slug=c.topic_slug,
                round=c.round,
                author=c.author,
                kind=c.kind,
                is_round_summary=c.is_round_summary,
                excerpt=c.excerpt,
                content=c.content,
                file_path=c.file_path,
                posted_at=c.posted_at,
                comment_seq=c.comment_seq,
                file_persona=c.file_persona,
                ack_valid=c.ack_valid,
                ack_error=c.ack_error,
            )
            for c in topic.comments
        ],
        action_items=[
            FsActionItemRead(
                id=a.id,
                title=a.title,
                owner=a.owner,
                status=a.status,
                evidence=a.evidence,
                reason=a.reason,
                created_at=a.created_at,
            )
            for a in topic.action_items
        ],
        action_items_error=topic.action_items_error,
    )


def _require_local_topic(workspace: Path, slug: str) -> Any:
    """本地解析话题文件夹；缺失时给出可操作错误（evidence 源）。"""
    from map_fs import parse_topic_dir

    parsed = parse_topic_dir(
        workspace / _content_root_name(workspace) / "topics" / slug, workspace
    )
    if parsed is None:
        typer.echo(
            f"Error: fs topic not found locally: {slug} "
            "(验证型写需要本地 map/ 文件夹作为事实源；run `map fs topic create` first)",
            err=True,
        )
        raise typer.Exit(1)
    return parsed


@fs_app.command("status")
def fs_status(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    """部署矩阵握手：本地 plane 概览 + server 可达性（local-fs / projection-cache / detached）。"""
    from cli.commands.doctor import warn_config_divergence
    from cli.main import _cli_options, _resolve_project, _run

    if _cli_options.get("format") in (None, "yaml"):
        warn_config_divergence(project_root=_cli_options.get("project_root"))

    workspace = _workspace()

    def action(c: MAPClient):
        from map_fs import scan_plane

        from cli.fs_projection import build_diff_payload

        pid = _resolve_project(c, project, project_key)
        plane = scan_plane(workspace, _content_root_name(workspace))
        status = c.fs_plane_status(pid)
        diff = build_diff_payload(c, pid=pid, workspace=workspace)
        return {
            "local": {
                "workspace": str(workspace),
                "content_root": _content_root_name(workspace),
                "topics": len(plane.topics),
                "experiments": len(plane.experiments),
                "content_hash": diff["local_content_hash"],
            },
            "server": status.model_dump(mode="json"),
            "sync_state": diff["sync_state"],
            "next": diff["next"],
        }

    def _render(result: dict) -> str:
        local, server = result["local"], result["server"]
        lines = [
            f"local    : {local['topics']} topic(s), {local['experiments']} experiment(s) "
            f"({local['workspace']}/{local['content_root']}) "
            f"hash={(local.get('content_hash') or '-')[:12]}",
            f"server   : mode={server['mode']} workspace_exists={server['workspace_exists']} "
            f"content_root_exists={server['content_root_exists']}",
        ]
        if server.get("projection_pushed_at"):
            lines.append(f"           projection pushed at {server['projection_pushed_at']}")
        if server.get("projection_revision"):
            lines.append(
                f"           revision={server['projection_revision']} "
                f"publisher={server.get('publisher_agent_id') or '-'} "
                f"consistency={server.get('consistency_model') or '-'}"
            )
        source = server.get("source") or {}
        if source:
            stale = "stale" if source.get("stale") else "fresh"
            reason = source.get("stale_reason") or "-"
            lines.append(
                f"           source={source.get('content_source')} "
                f"rev={source.get('source_revision') or '-'} {stale} reason={reason}"
            )
            if source.get("source_updated_at"):
                lines.append(f"           updated={source['source_updated_at']}")
        lines.append(f"sync     : {result.get('sync_state') or '-'}")
        if result.get("next"):
            lines.append(f"next     : {result['next']}")
        if server.get("hint"):
            lines.append(f"hint     : {server['hint']}")
        return "\n".join(lines)

    _run(action, table_renderer=_render)


def validated_write_flow(
    c: MAPClient,
    *,
    pid: uuid.UUID,
    action_name: str,
    topic: str,
    validate_call,
) -> dict:
    """验证型写核心流程（复用调用方 client）：validate → 本地写回 → commit。

    - server 校验权限与 ack 完整性（远程模式凭本地解析的 evidence）；
    - 写回永远发生在 CLI 本地（内容主权在文件系统）；
    - commit 凭 HMAC token 完成审计 + 通知 + 投影缓存刷新。
    """
    from map_fs import update_topic_index
    from map_types.schemas.fs import FsWriteCommitRequest

    workspace = _workspace()
    # evidence：本地解析快照（旧客户端兼容字段，远程校验只信已 CAS 发布的
    # 投影；validate 前的增量 sync 会先把本地变更发布上去并取回 revision）。
    parsed = _require_local_topic(workspace, topic)
    evidence = fs_topic_to_detail_read(parsed)
    index_path = (
        workspace / _content_root_name(workspace) / "topics" / topic / "index.md"
    )
    original_index = index_path.read_bytes()

    plane_status = c.fs_plane_status(pid)
    base_revision: int | None = None
    if plane_status.mode != "local-fs":
        # 增量同步（禁删）：远端独有对象不会被验证型写隐式清掉——全量 PUT
        # 会静默删除投影中本地缺失的对象，绕过 map fs sync 的 tombstone 门禁。
        from cli.fs_projection import sync_projection

        result = sync_projection(c, pid=pid, workspace=workspace, allow_deletes=False)
        if result.get("sync_state") == "skipped-deletes":
            typer.echo(
                "Warning: projection sync before validate skipped because remote "
                "objects would be deleted. Preview with `map fs diff`, then "
                "`map fs sync --yes`.",
                err=True,
            )
        base_revision = int(
            result.get("projection_revision") or result["base_revision"]
        )

    try:
        verdict = validate_call(c, pid, evidence, base_revision)
    except MAPHTTPError as exc:
        _render_validate_error(exc)
        raise
    update_topic_index(
        workspace, topic, content_root=_content_root_name(workspace), **verdict.fields
    )
    try:
        commit = c.fs_write_commit(
            pid,
            FsWriteCommitRequest(
                token=verdict.token,
                slug=topic,
                action=verdict.action,
                applied_fields=verdict.fields,
            ),
        )
    except Exception:
        # validate 成功但 CAS commit 失败时，不能留下未审计的本地状态。
        index_path.write_bytes(original_index)
        raise
    return {
        "action": commit.action,
        "slug": commit.slug,
        "fields": verdict.fields,
        "committed": commit.accepted,
        "projection_revision": commit.projection_revision,
        "flow": f"{action_name}: validate → local write-back → commit",
        "topic": verdict.topic.model_dump(mode="json"),
    }


def _run_validated_write(
    *,
    action_name: str,
    topic: str,
    project: uuid.UUID | None,
    project_key: str | None,
    validate_call,
) -> None:
    """``map fs advance-round|close`` 入口：包一层 client 构造与输出渲染。"""
    from cli.main import _resolve_project, _run

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        return validated_write_flow(
            c, pid=pid, action_name=action_name, topic=topic, validate_call=validate_call
        )

    _run(action)


@fs_app.command("advance-round")
def fs_advance_round(
    topic: str = typer.Option(..., "--topic", "--id"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    waive_ack: bool = typer.Option(False, "--waive-ack"),
    waive_reason: str | None = typer.Option(None, "--waive-reason"),
    mark_ready: bool = typer.Option(False, "--mark-ready"),
) -> None:
    """推进轮次：server 校验 ack 满员 → 本地写回 index.md → commit 审计。"""
    from map_types.schemas.fs import FsAdvanceRoundRequest

    def validate_call(
        c: MAPClient, pid: uuid.UUID, evidence, base_revision: int | None
    ):
        payload = FsAdvanceRoundRequest(
            waive_ack=waive_ack,
            waive_reason=waive_reason,
            mark_ready=mark_ready,
            base_revision=base_revision,
            evidence=evidence,
        )
        return c.fs_validate_advance_round(pid, topic, payload)

    _run_validated_write(
        action_name="advance-round",
        topic=topic,
        project=project,
        project_key=project_key,
        validate_call=validate_call,
    )


def _render_validate_error(exc: MAPHTTPError) -> None:
    """验证型写 409 → 逐行列出可操作依据（ack pending / 执行项未清零）。"""
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str) and detail.strip().startswith("{"):
        # map_client 把 error body 的 detail 统一 str()（client.py:183），
        # 结构化 409 的 dict 因此以 Python/repr 字符串形态到达；还原后再分支。
        try:
            parsed = json.loads(detail.strip())
        except ValueError:
            try:
                parsed = ast.literal_eval(detail.strip())
            except (ValueError, SyntaxError):
                parsed = None
        if isinstance(parsed, dict):
            detail = parsed
    if not isinstance(detail, dict):
        return  # 非结构化错误交由上层统一渲染
    if detail.get("error") == "round_ack_pending":
        # advance-round 409 → 缺/无效表态，带文件名+原因（A5）
        typer.echo("Error 409: round ack pending — 本轮仍有缺/无效表态（含原因）", err=True)
        for persona in detail.get("missing", []):
            reason = detail.get("missing_reasons", {}).get(persona) or "缺文件（未发言）"
            typer.echo(f"  - {persona}: {reason}", err=True)
        return
    if detail.get("error") == "action_items_open":
        # close 409 → 执行项未清零 / yaml 损坏（D2 唯一防线，A3）
        items = detail.get("items") or []
        if items:
            typer.echo("Error 409: action items 未清零 — 无法关闭（closed = 零尾款）", err=True)
            for item in items:
                typer.echo(
                    f"  - #{item['id']} {item['title']} (owner: {item['owner']}) — "
                    "用 `map topic action-item complete/cancel` 清零后再 close",
                    err=True,
                )
        else:
            typer.echo(f"Error 409: action-items.yaml 无法解析 — {detail.get('detail') or '未知原因'}", err=True)
            typer.echo(
                "  修复 action-items.yaml 后再 close（命令见 `map topic action-item --help`）",
                err=True,
            )


@fs_app.command("close")
def fs_close(
    topic: str = typer.Option(..., "--topic", "--id"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    reason: str | None = typer.Option(None, "--reason"),
    note: str | None = typer.Option(None, "--note"),
) -> None:
    """关闭话题：server 校验 → 本地写回 index.md status=closed → commit 审计。"""
    from map_types.schemas.fs import FsCloseRequest

    def validate_call(
        c: MAPClient, pid: uuid.UUID, evidence, base_revision: int | None
    ):
        payload = FsCloseRequest(
            close_reason=reason,
            close_note=note,
            base_revision=base_revision,
            evidence=evidence,
        )
        return c.fs_validate_close(pid, topic, payload)

    _run_validated_write(
        action_name="close",
        topic=topic,
        project=project,
        project_key=project_key,
        validate_call=validate_call,
    )


@fs_app.command("archive")
def fs_archive(
    topic: str = typer.Option(..., "--topic", "--id", help="话题 slug（--id 为别名，T2-P2）"),
    undo: bool = typer.Option(
        False, "--undo", help="还原归档话题（archive 目录移回 map/topics/）"
    ),
) -> None:
    """归档 FS 话题：校验（已 closed / 目标不存在）→ git mv 移入 map/archive/ → 自动重建索引。

    薄命令（v0.14 M60 定稿）：零 API；git workspace 下 ``git mv`` 前置执行
    （stage 后 ``git status`` 显示 ``renamed:``），非 git fallback ``os.rename``；
    不承载索引维护逻辑，索引一致性由自动 rebuild 达成（M61 生成式投影）。
    """
    from map_fs.archive import (
        ArchiveStateError,
        archive_topic,
        find_experiment_references,
        rebuild_archive_index,
        unarchive_topic,
    )

    workspace = _workspace()
    root = _content_root_name(workspace)
    try:
        if undo:
            restored = unarchive_topic(workspace, topic, content_root=root)
            index = rebuild_archive_index(workspace, root)
            typer.echo(f"Restored {restored.relative_to(workspace)}")
            typer.echo(f"Rebuilt {index.relative_to(workspace)} (entry removed)")
            return
        refs = find_experiment_references(workspace, topic, content_root=root)
        if refs:
            typer.echo(
                f"Warning: '{topic}' is referenced by experiment files below "
                "(weak local check — experiment phase lives in DB and is not "
                "visible without API; archive proceeds):\n  " + "\n  ".join(refs),
                err=True,
            )
        dst = archive_topic(workspace, topic, content_root=root)
    except ArchiveStateError as err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(2) from err
    index = rebuild_archive_index(workspace, root)
    typer.echo(f"Archived {topic} -> {dst.relative_to(workspace)}")
    typer.echo(f"Rebuilt {index.relative_to(workspace)} (entry reflected)")


@fs_app.command("archive-index")
def fs_archive_index(
    rebuild: bool = typer.Option(
        False, "--rebuild", help="全量重建（生成式投影，唯一模式）"
    ),
) -> None:
    """重建 ``map/archive/INDEX.md``：扫描 archive/topics/ 全量重生成（v0.14 M61）。

    双形态解析：目录形态 ``<slug>/`` 读 index.md frontmatter；legacy export
    标题命名单文件读头部字段。幂等——重复执行结果一致，无增量状态。
    """
    from map_fs.archive import rebuild_archive_index, scan_archive_entries

    workspace = _workspace()
    root = _content_root_name(workspace)
    if not rebuild:
        typer.echo(
            "Error: --rebuild is required (INDEX.md is a generative projection; "
            "there is no incremental mode)",
            err=True,
        )
        raise typer.Exit(2)
    entries = scan_archive_entries(workspace, root)
    index = rebuild_archive_index(workspace, root)
    typer.echo(f"Rebuilt {index.relative_to(workspace)} ({len(entries)} entries)")


@fs_app.command("push")
def fs_push(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    yes: bool = typer.Option(False, "--yes", help="Confirm remote deletes (tombstones)"),
) -> None:
    """Deprecated alias for ``map fs sync --full``. Prefer ``map fs sync``."""
    typer.echo(
        "Warning: `map fs push` is deprecated; use `map fs sync --full`.",
        err=True,
    )
    _run_fs_sync(project=project, project_key=project_key, dry_run=False, full=True, yes=yes)


def _run_fs_sync(
    *,
    project: uuid.UUID | None,
    project_key: str | None,
    dry_run: bool,
    full: bool,
    yes: bool,
) -> None:
    from cli.fs_projection import sync_projection
    from cli.main import _resolve_project, _run

    workspace = _workspace()

    def action(c: MAPClient):
        return sync_projection(
            c,
            pid=_resolve_project(c, project, project_key),
            workspace=workspace,
            dry_run=dry_run,
            full=full,
            yes=yes,
        )

    _run(action)


@fs_app.command("diff")
def fs_diff(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    """Compare local map/ with the server projection (summary only, no bodies)."""
    from cli.fs_projection import build_diff_payload
    from cli.main import _resolve_project, _run

    workspace = _workspace()

    def action(c: MAPClient):
        return build_diff_payload(
            c, pid=_resolve_project(c, project, project_key), workspace=workspace
        )

    def _render(result: dict) -> str:
        lines = [
            f"sync_state : {result['sync_state']}",
            f"revision   : {result['base_revision'] or '(none)'}",
            f"local_hash : {result['local_content_hash'][:12]}…",
            f"server_hash: {(result['server_content_hash'] or '-')[:12]}",
        ]
        for label, key in (("added", "added"), ("modified", "modified"), ("deleted", "deleted")):
            rows = result[key]
            if rows:
                slugs = ", ".join(f"{r['kind']}:{r['slug']}" for r in rows)
                lines.append(f"{label:10}: {slugs}")
        for blocker in result.get("blockers") or []:
            lines.append(f"blocker   : {blocker}")
        if result.get("next"):
            lines.append(f"next      : {result['next']}")
        return "\n".join(lines)

    _run(action, table_renderer=_render)


@fs_app.command("sync")
def fs_sync(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    full: bool = typer.Option(False, "--full", help="Upsert every local object, not just the delta"),
    yes: bool = typer.Option(False, "--yes", help="Confirm remote deletes (tombstones)"),
) -> None:
    """Publish local map/ to the server projection with CAS (delta + explicit deletes)."""
    _run_fs_sync(
        project=project,
        project_key=project_key,
        dry_run=dry_run,
        full=full,
        yes=yes,
    )


def _experiment_read(e: Any) -> Any:
    from map_types.schemas.fs import FsExperimentRead

    return FsExperimentRead(
        id=e.id,
        slug=e.slug,
        title=e.title,
        description=e.description,
        phase=e.phase,
        creator=e.creator,
        created_at=e.created_at,
        dir_path=e.dir_path,
        plan_path=e.plan_path,
        log_path=e.log_path,
        review_path=e.review_path,
    )


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
