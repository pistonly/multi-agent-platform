"""Topic ``--id`` routing & local-FS scan helpers — T33 extraction.

Moved verbatim from ``cli/commands/topic.py`` (M51 routing layer + M56
FS-degradation hints + M58 DB-write-retirement guidance). ``topic.py``
keeps the command definitions and imports the symbols it uses; these
helpers have no ``commands_topic`` monkeypatch surface (tests import
them directly from this module, see ``tests/test_topic_routing.py``).
"""
from __future__ import annotations

import ast
import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import typer
import yaml
from map_client.client import MAPClient

from cli import runner  # module ref: test monkeypatch surface (T23)

if TYPE_CHECKING:
    from map_client.exceptions import MAPHTTPError

# ---------------------------------------------------------------------------
# M51：--id 路由层（DB 话题 vs FS 事实源话题统一入口）
# ---------------------------------------------------------------------------


def _looks_like_uuid(ref: str) -> bool:
    try:
        uuid.UUID(ref)
    except ValueError:
        return False
    return True


def _fs_workspace_and_root() -> tuple[Path, str]:
    from cli.commands.fs import _content_root_name, _workspace

    workspace = _workspace()
    return workspace, _content_root_name(workspace)


def _optional_workspace() -> Path | None:
    """``.map/`` 缺失时返回 None，不退出——list 合并需要能退化成纯 API。

    实验 e7244a91（A1）：经 ProjectContext 单点解析，不再裸 ``find_map_dir(None)``。
    """
    from cli.project_context import optional_context

    context = optional_context()
    return None if context is None else context.workspace_root


# 与 server/services/fs_source_service.py 同源：persona 名 → 稳定展示用 uuid。
_PERSONA_NS = uuid.uuid5(uuid.NAMESPACE_URL, "map-fs-persona")


def _fs_topic_to_summary(topic: Any, project_id: uuid.UUID) -> Any:
    from datetime import datetime, timezone

    from map_types.enums import TopicStatus
    from map_types.schemas import TopicSummaryRead

    last = topic.comments[-1] if topic.comments else None
    now = datetime.now(timezone.utc)
    created = topic.created_at or topic.updated_at or now
    return TopicSummaryRead(
        id=topic.id,
        project_id=project_id,
        creator_agent_id=uuid.uuid5(_PERSONA_NS, topic.creator),
        creator_name=topic.creator,
        title=topic.title,
        description=topic.description or None,
        slug=topic.slug,
        status=TopicStatus(topic.status),
        pinned=False,
        discussion_round=topic.round,
        round_summary_count=sum(1 for c in topic.comments if c.is_round_summary),
        comment_count=len(topic.comments),
        experiment_count=0,
        last_comment_id=last.id if last is not None else None,
        last_comment_author_agent_id=(
            uuid.uuid5(_PERSONA_NS, last.author) if last is not None else None
        ),
        last_comment_author_name=last.author if last is not None else None,
        last_comment_excerpt=last.excerpt if last is not None else None,
        my_comment_count=None,
        dismissed_at=None,
        stale_since=None,
        created_at=created,
        updated_at=topic.updated_at or now,
        archived_at=None,
        close_reason=None,
        close_note=None,
    )


def _fs_topic_to_detail(topic: Any) -> Any:
    from cli.commands.fs import fs_topic_to_detail_read

    return fs_topic_to_detail_read(topic)


def _scan_local_fs_summaries(project_id: uuid.UUID) -> list[Any]:
    from map_fs import scan_plane

    from cli.commands.fs import _content_root_name

    workspace = _optional_workspace()
    if workspace is None:
        return []
    return [
        _fs_topic_to_summary(t, project_id)
        for t in scan_plane(workspace, _content_root_name(workspace)).topics
    ]


def _merge_topic_summaries(local_fs: list[Any], api_topics: list[Any]) -> list[Any]:
    """API 能扫到的话题以 API 为准（agent id / experiment_count 更完整）；
    本地独有的 slug（例如 Docker API 读不到宿主机 map/）补进列表。"""
    api_slugs = {t.slug for t in api_topics if t.slug}
    api_ids = {t.id for t in api_topics}
    extras = [t for t in local_fs if t.slug not in api_slugs and t.id not in api_ids]
    return extras + list(api_topics)


def _local_creator_match(
    summary: Any, creator: str | None, creator_agent_id: uuid.UUID | None
) -> bool:
    if creator is None and creator_agent_id is None:
        return True
    if creator and not _looks_like_uuid(creator) and summary.creator_name == creator:
        return True
    return creator_agent_id is not None and summary.creator_agent_id == creator_agent_id


def _filter_local_summaries(
    topics: list[Any],
    *,
    status: str | None,
    creator: str | None,
    creator_agent_id: uuid.UUID | None,
    q: str | None,
) -> list[Any]:
    from map_types.enums import TopicStatus

    st = TopicStatus(status) if status else None
    needle = q.lower() if q else None
    out: list[Any] = []
    for t in topics:
        if st is not None and t.status != st:
            continue
        if not _local_creator_match(t, creator, creator_agent_id):
            continue
        if needle and needle not in t.title.lower() and not (
            t.slug and needle in t.slug.lower()
        ):
            continue
        out.append(t)
    return out


def _list_api_topics_all(c: MAPClient, pid: uuid.UUID, **kwargs: Any) -> list[Any]:
    page = 1
    acc: list[Any] = []
    while True:
        batch, total = c.list_topics_page(pid, page=page, page_size=100, **kwargs)
        acc.extend(batch)
        if not batch or len(acc) >= total:
            break
        page += 1
        if page > 100:
            break
    return acc


def _slice_page(items: list[Any], page: int, page_size: int) -> list[Any]:
    start = (page - 1) * page_size
    return items[start : start + page_size]


def _should_scan_local_fs(
    project: uuid.UUID | None,
    project_key: str | None,
    resolved_pid: uuid.UUID,
) -> bool:
    """只在列出当前 workspace 所属项目时合并本地 map/。"""
    workspace = _optional_workspace()
    if workspace is None:
        return False
    if project is None and project_key is None:
        return True
    map_cfg = workspace / ".map" / "config.yaml"
    if not map_cfg.is_file():
        return False
    try:
        data = yaml.safe_load(map_cfg.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return False
    if not isinstance(data, dict):
        return False
    cfg_id = data.get("project_id")
    if cfg_id and str(cfg_id) == str(resolved_pid):
        return True
    cfg_key = data.get("project_key")
    return bool(project_key) and cfg_key == project_key


def _fs_slug_by_uuid(ref: str) -> str | None:
    """uuid5 id → slug 本地反查（scan_plane 实时解析，零 API）。"""
    from map_fs import scan_plane

    try:
        ref_uuid = uuid.UUID(ref)
    except ValueError:
        return None
    workspace, root = _fs_workspace_and_root()
    for t in scan_plane(workspace, root).topics:
        if t.id == ref_uuid:
            return t.slug
    return None


def _db_uuid_by_slug(c: MAPClient, slug: str) -> uuid.UUID | None:

    pid = runner._resolve_project(c, None, None)
    for t in c.list_topics(pid, page_size=100):
        if t.slug == slug:
            return t.id
    return None


def _archived_slug_hint(ref: str, ref_uuid: uuid.UUID | None) -> str | None:
    """FS 未命中时的归档 fallback：ref 命中 map/archive/topics/ 则返回 slug（v0.14 读路径）。

    slug 直查目录存在性；uuid5 引用扫归档目录反推（uuid5 由 slug 确定派生）。
    返回非空时调用方输出「已归档」指引（含 --undo 还原路径），不泄露 ghost。
    """
    from map_fs import topic_id_for_slug

    workspace, root = _fs_workspace_and_root()
    archive_dir = workspace / root / "archive" / "topics"
    if not archive_dir.is_dir():
        return None
    if ref_uuid is None:
        return ref if (archive_dir / ref).is_dir() else None
    for entry in sorted(archive_dir.iterdir()):
        if entry.is_dir() and topic_id_for_slug(entry.name) == ref_uuid:
            return entry.name
    return None


def _exit_not_found(message: str, ref: str, ref_uuid: uuid.UUID | None) -> NoReturn:
    """统一的 topic 未找到出口：命中归档目录时升级为「已归档」指引（v0.14）。"""
    hint = _archived_slug_hint(ref, ref_uuid)
    if hint is not None:
        typer.echo(
            f"Error: topic '{hint}' is archived (map/archive/topics/{hint}/) — "
            f"restore via `map topic archive --topic {hint} --undo` to resume; "
            "archived topics are read-only via the archive folder",
            err=True,
        )
        raise typer.Exit(1)
    typer.echo(message, err=True)
    raise typer.Exit(1)


def _resolve_topic_ref(c: MAPClient, ref: str, storage: str | None) -> tuple[str, str | uuid.UUID]:
    """解析 --id 为 ('db', uuid) 或 ('fs', slug)。

    自动路由：uuid → DB API 优先（404 后本地反查 FS uuid5）；
    slug → FS 优先（map/topics/<slug>/ 存在即 FS），否则 DB slug 匹配。
    --storage fs|db 显式覆盖，不命中即报错。
    """
    from map_client.exceptions import MAPNotFoundError

    if storage not in (None, "fs", "db"):
        typer.echo(f"Error: --storage must be 'fs' or 'db', got '{storage}'", err=True)
        raise typer.Exit(2)

    is_uuid = _looks_like_uuid(ref)
    ref_uuid = uuid.UUID(ref) if is_uuid else None

    def db_hit() -> uuid.UUID | None:
        if ref_uuid is None:
            return _db_uuid_by_slug(c, ref)
        try:
            c.get_topic(ref_uuid)
        except MAPNotFoundError:
            return None
        return ref_uuid

    def fs_hit() -> str | None:
        if ref_uuid is not None:
            return _fs_slug_by_uuid(ref)
        from map_fs import parse_topic_dir

        workspace, root = _fs_workspace_and_root()
        t = parse_topic_dir(workspace / root / "topics" / ref, workspace)
        return t.slug if t is not None else None

    if storage == "fs":
        slug = fs_hit()
        if slug is not None:
            return ("fs", slug)
        _exit_not_found(f"Error: topic not found: {ref} (see `map topic list`)", ref, ref_uuid)
    if storage == "db":
        tid = db_hit()
        if tid is not None:
            return ("db", tid)
        typer.echo(f"Error: DB topic not found: {ref} (see `map topic list`)", err=True)
        raise typer.Exit(1)
    if is_uuid:
        tid = db_hit()
        if tid is not None:
            return ("db", tid)
        slug = fs_hit()
        if slug is not None:
            return ("fs", slug)
        _exit_not_found(f"Error: topic not found (DB API and map/ folders): {ref}", ref, ref_uuid)
    slug = fs_hit()
    if slug is not None:
        return ("fs", slug)
    tid = db_hit()
    if tid is not None:
        return ("db", tid)
    _exit_not_found(
        f"Error: topic not found: {ref} (no map/topics/{ref}/ folder and no DB slug "
        "match; see `map topic list`)",
        ref,
        None,
    )


# ---------------------------------------------------------------------------
# M56：六命令接入三态路由后，对 fs 目标的降级行为（二分：通知投影类 / 状态变迁类）
# ---------------------------------------------------------------------------

_FS_TRANSITION_HINTS: dict[str, str] = {
    "resolve": (
        "record the decision via close instead: "
        "`map topic close --id <slug> --reason <code> --note <decision>` "
        "(close_reason carries the decision)"
    ),
    "rollback-round": (
        "FS rounds are file facts — remove/rename the round<N>-<persona>.md "
        "files under map/topics/<slug>/ directly (see file-reference.md)"
    ),
    "reopen": (
        "FS topic status lives in map/topics/<slug>/index.md — edit the "
        "status field directly"
    ),
}


def _fs_transition_rejected(command: str, slug: str) -> NoReturn:
    """状态变迁类命令对 fs 目标统一拒绝：无 fs 等价 API，静默 no-op 会让 agent
    误以为状态变迁已发生（M56B）。"""
    typer.echo(
        f"Error: `topic {command}` targets DB topics only; '{slug}' is an FS "
        f"(map/) topic — {_FS_TRANSITION_HINTS[command]}",
        err=True,
    )
    raise typer.Exit(2)


def _fs_projection_noop(command: str, slug: str) -> NoReturn:
    """通知投影类命令对 fs 目标统一 no-op 提示：fs 话题无 DB todos 投影动作，
    pending 项靠写 round 文件清理，不静默（M56B）。"""
    typer.echo(
        f"No-op: FS topic '{slug}' has no DB todos projection; `topic {command}` "
        "only affects DB topics. FS pending items (e.g. fs_file_missing) clear "
        "by writing round files (see `map topic work`)."
    )
    raise typer.Exit(0)


# v0.13 M58：DB 话题写路径整体退役。退役面 = resolve / rollback-round /
# reopen / archive 四命令全量 + comment / advance-round / close 的 DB 分支
#（含显式 --storage db）。``topic create`` 已转发到本地 map/ 文件夹。
# 读路径（show/list/progress）与 dismiss/read/mark-seen/migrate 保留。
# 一律引导性错误（exit 2），不静默成功。
_DB_WRITE_RETIRED_HINTS: dict[str, str] = {
    "create": (
        "create topics instead: "
        "`map topic create --title ... --slug <name> --participants <a,b>` "
        "(writes map/topics/<slug>/)"
    ),
    "resolve": (
        "decisions are carried by the close note: "
        "`map topic close --topic <slug> --reason <code> --note <decision>`; "
        "legacy DB topic: `map topic migrate --id <uuid> --slug <name>` first"
    ),
    "advance-round": (
        "advance via `map topic advance-round --topic <slug>`; "
        "legacy DB topic: `map topic migrate --id <uuid> --slug <name>` first"
    ),
    "rollback-round": (
        "rounds are file facts — remove the round<N>-*.md files and fix "
        "index.md round/participants consistency (see file-reference.md); "
        "legacy DB topic: `map topic migrate` first"
    ),
    "comment": (
        "comment via `map topic comment --topic <slug> --file <md>`; "
        "legacy DB topic: `map topic migrate --id <uuid> --slug <name>` first"
    ),
    "close": (
        "close via `map topic close --topic <slug> --reason <code> --note ...`; "
        "legacy DB topic: `map topic migrate --id <uuid> --slug <name>` first"
    ),
    "reopen": (
        "topic status lives in map/topics/<slug>/index.md — edit `status` "
        "directly and note the reason in the close note or a new speech; "
        "legacy DB topic: `map topic migrate` first"
    ),
    "archive": (
        "archive via `map topic archive --topic <slug>` "
        "(validates closed status, git mv, auto-rebuilds archive INDEX); "
        "legacy DB topics stay readable via `topic show` (archive flag no longer maintained)"
    ),
    "archive-undo": (
        "restore an archived topic via `map topic archive --topic <slug> --undo`; "
        "index consistency is rebuilt automatically"
    ),
}


def _db_write_retired(command: str, target: str | None = None) -> NoReturn:
    """DB 话题写路径退役统一拒绝（v0.13 M58）。

    与 ``_fs_transition_rejected``（M56B，FS 目标跑状态变迁命令）互为镜像：
    这里拒绝的是 DB 侧写调用。引导性错误，exit 2，不静默成功。
    """
    where = f" for '{target}'" if target else ""
    typer.echo(
        f"Error: `topic {command}` DB write path retired in v0.13 M58{where} — "
        f"{_DB_WRITE_RETIRED_HINTS[command]}",
        err=True,
    )
    raise typer.Exit(2)


# ---------------------------------------------------------------------------
# 实验 0f271f7e A6：验证型写 409 渲染自 cli/commands/fs.py 拆入（fs.py 800
# 行 cap）。``cli.commands.fs`` 顶部 re-import 保持既有 import 面（fs_write_flow
# 与多个测试经 ``cli.commands.fs._render_validate_error`` 属性访问）。
# ---------------------------------------------------------------------------


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
