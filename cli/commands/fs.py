"""Helpers for folder-backed topic writes (CLI surface is ``map topic`` / ``map sync``).

内容根目录：``.map/config.yaml`` 的 ``content_root``（默认 ``map``），
即 ``<workspace>/map/``。用户命令不暴露 fs：话题走 ``map topic``，
投影同步走 ``map sync publish`` / ``diff`` / ``check``。
"""

from __future__ import annotations

import ast
import json
import uuid
from pathlib import Path
from typing import Any

import typer
import yaml
from map_client.client import MAPClient
from map_client.exceptions import MAPHTTPError

from cli import runner  # module ref: test monkeypatch surface (T23)
from cli.table_render import render_table, truncate


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

    全局 ``map --persona <name> topic ...`` 与其他命令组语义一致。
    """
    if persona:
        return persona
    try:
        from cli.main import _cli_options  # runtime state (monkeypatch surface)

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
        typer.echo(f"Error: topic not found: {slug} (run `map topic create` first)", err=True)
        raise typer.Exit(1)
    return topic.round_number


# ---------------------------------------------------------------------------
# 纯文件操作（离线）
# ---------------------------------------------------------------------------


def fs_init(workspace: Path | None = None) -> None:
    """创建内容根目录结构：<content_root>/topics 与 <content_root>/experiments。"""
    from map_fs import DEFAULT_CONTENT_ROOT

    workspace = workspace or _workspace()
    root = workspace / _content_root_name(workspace)
    for sub in ("topics", "experiments"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    typer.echo(
        f"Initialized {root / 'topics'} and {root / 'experiments'} (content_root={root.name or DEFAULT_CONTENT_ROOT})"
    )


# ---------------------------------------------------------------------------
# Local plane（plane: local）：无 server 的验证型写
# ---------------------------------------------------------------------------


def is_local_plane(workspace: Path | None = None) -> bool:
    """当前项目是否为离线本地平面（config.yaml ``plane: local``）；解析错误按 remote。"""
    try:
        from map_client.project_config import find_map_dir, load_project_map_config

        map_dir = find_map_dir(workspace)
        if map_dir is None:
            return False
        return load_project_map_config(map_dir=map_dir).plane == "local"
    except ValueError:
        return False


def local_topic_slug(ref: str) -> str:
    """local plane 下把话题引用解析为 slug：目录名命中或 uuid5 本地反查。"""
    from map_fs import scan_plane

    workspace = _workspace()
    root = _content_root_name(workspace)
    if (workspace / root / "topics" / ref).is_dir():
        return ref
    try:
        ref_uuid = uuid.UUID(ref)
    except ValueError:
        ref_uuid = None
    if ref_uuid is not None:
        for t in scan_plane(workspace, root).topics:
            if t.id == ref_uuid:
                return t.slug
    typer.echo(
        f"Error: plane: local — '{ref}' is not a local topic "
        "(use the topic slug or its uuid5 id; see `map topic list`)",
        err=True,
    )
    raise typer.Exit(1)


def local_actor_persona() -> str:
    """local plane actor：子命令/全局 --persona > default_persona，按 agents.yaml 归一（无需 token）。"""
    from map_client.project_config import find_map_dir, load_project_map_config

    from cli.main import _cli_options  # runtime state (monkeypatch surface)

    cfg = load_project_map_config(map_dir=find_map_dir(None))
    persona = _cli_options.get("persona") or cfg.default_persona
    return cfg.resolve_persona(str(persona))


def write_new_fs_topic(
    *,
    title: str,
    slug: str | None,
    description: str = "",
    participants: str | None = None,
    creator: str | None = None,
    force: bool = False,
) -> Path:
    """离线创建话题文件夹 + index.md。``map topic create`` 使用此函数。

    slug 为空时由 title 生成（两个入口行为一致）。title 为空直接报错退出——
    部分 typer/click 版本组合不强制校验必填 CLI 选项，缺失的 ``--title``
    会以 None 穿透到函数体（回归见 tests/test_topic_routing.py）。

    ``force=True`` 覆盖既有 index.md（保留 created_at / 评论 / experiments），
    与 ``write_topic_index(overwrite=True)`` 路径一致；``FileExistsError``
    留给上层 CLI 捕获并以 exit 1 + stderr 'already exists' 退出。
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
        overwrite=force,
    )


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
    """离线创建话题文件夹 + index.md（不调 API）。日常写入口；map topic create 为隐藏兼容别名。"""
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


def fs_comment(
    topic: str = typer.Option(..., "--topic", "--id", help="话题 slug（--id 为别名，T2-P2）"),
    body: str | None = typer.Option(None, "--body", help="评论正文（与 --file 二选一）"),
    file: Path | None = typer.Option(None, "--file", help="从 MD 文件读正文"),
    persona: str | None = typer.Option(None, "--persona"),
    round_number: int | None = typer.Option(None, "--round", help="默认取话题当前轮次"),
    round_summary: bool = typer.Option(False, "--round-summary"),
    force: bool = typer.Option(
        False,
        "--force",
        help="覆盖已有评论文件（破坏 immutable 约定）；不豁免 frontmatter 前置校验（W1）",
    ),
    no_sync: bool = typer.Option(False, "--no-sync", help="Skip remote projection sync after the local write"),
) -> None:
    """离线写普通发言或独立 Round Summary（不调 API）。"""
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
    except ValueError as err:
        # W1 写路径前置校验：body 自带 frontmatter（--force 不豁免）
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(2) from err
    typer.echo(f"Wrote {path}")
    from cli.fs_projection import maybe_auto_sync

    maybe_auto_sync(no_sync=no_sync, workspace=workspace)


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
    typer.echo(render_table(headers, rows) if rows else "(no topics)")


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
                f"restore via `map topic archive --topic {topic} --undo` to resume",
                err=True,
            )
            raise typer.Exit(1)
        typer.echo(f"Error: topic not found: {topic}", err=True)
        raise typer.Exit(1)
    typer.echo(f"# {t.title}  [{t.slug}]")
    typer.echo(f"status={t.status} round={t.round} creator={t.creator} dir={t.dir_path}")
    typer.echo(f"participants: {', '.join(t.participants)}")
    if t.anomalies:
        # R1/V1 读路径报告：只报告不阻断不改写（存量脏文件保留历史）
        typer.echo(
            f"anomalies: {len(t.anomalies)} "
            "(invalid=字段存在但非法; lite=缺失; 只报告不阻断读, 详见 `map topic anomalies`)"
        )
        a_headers = ["File", "Level", "Reason"]
        a_rows = [[a.file, a.level, a.reason] for a in t.anomalies]
        typer.echo(render_table(a_headers, a_rows))
    if not t.comments:
        typer.echo("(no comments)")
        return
    headers = ["Round", "Author", "Excerpt", "File"]
    rows = [[str(c.round), c.author, truncate(c.excerpt, 44), c.file_path] for c in t.comments]
    typer.echo(render_table(headers, rows))
    if full:
        for c in t.comments:
            typer.echo(f"\n===== {c.file_path} =====\n{c.content}")


def fs_anomalies(
    format: str = typer.Option(
        "table", "--format", help="Output format: table | yaml | json (v0.12 M54A)."
    ),
) -> None:
    """离线扫描全部话题 round 文件的 frontmatter anomaly（只报告，不阻断不改写）。

    invalid=字段存在但非法（author/round/posted_at 与文件名或语义不符）；
    lite=缺失（frontmatter 整块缺失或 posted_at 缺失）。
    """
    import yaml as _yaml
    from map_fs import scan_plane

    workspace = _workspace()
    plane = scan_plane(workspace, _content_root_name(workspace))
    rows = [
        {"topic": t.slug, "status": t.status, "file": a.file, "level": a.level, "reason": a.reason}
        for t in plane.topics
        for a in t.anomalies
    ]
    if format == "json":
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if format == "yaml":
        typer.echo(_yaml.safe_dump(rows, allow_unicode=True, sort_keys=False).strip())
        return
    if not rows:
        typer.echo("(no anomalies)")
        return
    headers = ["Topic", "File", "Level", "Reason"]
    table_rows = [[r["topic"], r["file"], r["level"], truncate(r["reason"], 52)] for r in rows]
    typer.echo(render_table(headers, table_rows))


def fs_work(persona: str | None = typer.Option(None, "--persona")) -> None:
    """离线推导 persona 待办（纯函数：文件存在性 → pending_topic_reply 等）。"""
    from map_fs import derive_work, scan_plane

    workspace = _workspace()
    who = _persona(persona)
    plane = scan_plane(workspace, _content_root_name(workspace))
    items = [i for t in plane.topics for i in derive_work(t, who)]
    if not items:
        typer.echo(f"(no folder work for {who})")
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
    """本地解析话题文件夹；缺失时给出可操作错误（evidence 源）。

    顺带从 :func:`map_fs.scan_plane` 关联实验列表（``experiment.topic ==
    topic.slug``）注入 ``FsTopic.experiments``，供 close 门禁做 terminal
    校验（实验 cli-fs-topic-lifecycle-invariants A4）。空列表或字段缺失
    → 由 validation 层放行。
    """
    from map_fs import parse_topic_dir, scan_plane

    parsed = parse_topic_dir(
        workspace / _content_root_name(workspace) / "topics" / slug, workspace
    )
    if parsed is None:
        typer.echo(
            f"Error: topic not found locally: {slug} "
            "(验证型写需要本地 map/ 文件夹作为事实源；run `map topic create` first)",
            err=True,
        )
        raise typer.Exit(1)
    # 反查关联实验：FsPlane 已扫到全量实验，按 topic slug 字段筛选
    try:
        plane = scan_plane(workspace, _content_root_name(workspace))
    except Exception:
        plane = None
    if plane is not None:
        parsed.experiments = [
            exp for exp in plane.experiments if getattr(exp, "topic", "") == slug
        ]
    return parsed


def fs_status(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    """部署矩阵握手：本地 plane 概览 + server 可达性（local-fs / projection-cache / detached）。"""
    from cli.commands.doctor import warn_config_divergence
    from cli.main import _cli_options  # runtime state (monkeypatch surface)

    if _cli_options.get("format") in (None, "yaml"):
        warn_config_divergence(project_root=_cli_options.get("project_root"))

    workspace = _workspace()

    def action(c: MAPClient):
        from map_fs import scan_plane

        from cli.fs_projection import build_diff_payload

        pid = runner._resolve_project(c, project, project_key)
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

    runner._run(action, table_renderer=_render)


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

# T45: 验证型写原语与投影同步/迁移拆至 cli.fs_write_flow / cli.fs_sync；
# 此处 re-import（facade）保持 ``cli.commands.fs`` 既有 import 面——外部
# lazy import（cli/commands/sync.py、topic.py、fs_projection.py、
# topic_migrate.py）与测试仍经本模块引用这些符号。
from cli.fs_sync import (  # noqa: E402,F401
    _experiment_read,
    _run_fs_sync,
    _topic_index_meta,
    fs_diff,
    fs_migrate_from_docs,
    fs_push,
    fs_sync,
)
from cli.fs_write_flow import (  # noqa: E402,F401
    _append_local_audit,
    _render_local_validate_error,
    _run_validated_write,
    local_validated_write_flow,
    validated_write_flow,
)
