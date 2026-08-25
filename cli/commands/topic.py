"""``map topic ...`` + ``map mention ...`` + ``map todo ...`` sub-apps — arch PR6.

Three sub-apps that share the "topic work items" domain:

* ``map topic ...`` — unified topic facade (list / show / create / comment /
  advance-round / close / progress / dismiss / read / mark-seen / migrate).
  Writes go to ``map/topics/<slug>/``; ``list``/``show`` merge local folders with
  leftover DB topics from the API. v0.13 M58: DB write paths retired —
  resolve / rollback-round / reopen / archive (and the DB branches of
  comment / advance-round / close) reject with guidance.
* ``map mention ...`` — personal @mention todos (dismiss / list /
  dismiss-all / reconcile-stale stub).
* ``map todo ...`` — explicit_only todo partition clear router
  (notification / action_item / my_open_topics / unread_change).

All command bodies lazy-import ``cli.main._run`` and friends to break
the ``cli.main ↔ cli.commands.*`` import cycle.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, NoReturn

import typer
import yaml
from map_client.client import MAPClient

from cli.table_render import enum_value, format_datetime, render_table, short_uuid, truncate

topic_app = typer.Typer(
    help=(
        "Topic commands (v0.13 M58: FS-only writes). Reads (show/list/progress) and "
        "migrate/dismiss stay DB-id based; write commands route FS targets to file writes "
        "and reject retired DB paths with guidance."
    ),
    rich_markup_mode=None,
)
mention_app = typer.Typer(help="Mention todo commands")
todo_app = typer.Typer(help="Todo partition clear routing (explicit_only buckets)")
# FS 话题执行项（action-items.yaml）——收敛时落盘，close 门禁校验清零
action_item_app = typer.Typer(
    help=(
        "FS 话题执行项(action-items.yaml)命令：add / complete / cancel / list。"
        "收敛时由 host 落盘 open 项，owner 完成/取消后清零，close 门禁校验无 open 才放行。"
    ),
    rich_markup_mode=None,
)
topic_app.add_typer(action_item_app, name="action-item")

_STORAGE_HELP = (
    "Route --id explicitly: 'fs' (map/ folder topic) or 'db' (platform DB). "
    "Default auto-routing: uuid -> DB first, then FS uuid5; slug -> FS first, then DB slug. "
    "v0.13 M58: explicit 'db' on write commands is rejected with guidance (DB write paths retired)."
)


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
    """``.map/`` 缺失时返回 None，不退出——list 合并需要能退化成纯 API。"""
    from map_client.project_config import find_map_dir

    map_dir = find_map_dir(None)
    return None if map_dir is None else map_dir.parent


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
    from cli.main import _resolve_project

    pid = _resolve_project(c, None, None)
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
            f"restore via `map fs archive --topic {hint} --undo` to resume; "
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
        _exit_not_found(f"Error: fs topic not found: {ref} (see `map fs list`)", ref, ref_uuid)
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
        "match; see `map fs list` / `map topic list`)",
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
        "by writing round files (see `map fs work`)."
    )
    raise typer.Exit(0)


# v0.13 M58：DB 话题写路径整体退役。退役面 = resolve / rollback-round /
# reopen / archive 四命令全量 + comment / advance-round / close 的 DB 分支
#（含显式 --storage db）。``topic create`` 已转发到本地 map/ 文件夹。
# 读路径（show/list/progress）与 dismiss/read/mark-seen/migrate 保留。
# 一律引导性错误（exit 2），不静默成功。
_DB_WRITE_RETIRED_HINTS: dict[str, str] = {
    "create": (
        "create FS topics instead: "
        "`map topic create --title ... --slug <name> --participants <a,b>` "
        "(or `map fs topic-create`)"
    ),
    "resolve": (
        "decisions are carried by the FS close note: "
        "`map fs close --topic <slug> --reason <code> --note <decision>`; "
        "legacy DB topic: `map topic migrate --id <uuid> --slug <name>` first"
    ),
    "advance-round": (
        "FS topics advance via `map fs advance-round --topic <slug>` "
        "(or `map topic advance-round --id <slug>`); "
        "legacy DB topic: `map topic migrate --id <uuid> --slug <name>` first"
    ),
    "rollback-round": (
        "FS rounds are file facts — remove the round<N>-*.md files and fix "
        "index.md round/participants consistency (see file-reference.md); "
        "legacy DB topic: `map topic migrate` first"
    ),
    "comment": (
        "comment FS topics via `map fs comment --topic <slug> --file <md>` "
        "(or `map topic comment --id <slug>`); "
        "legacy DB topic: `map topic migrate --id <uuid> --slug <name>` first"
    ),
    "close": (
        "close FS topics via `map fs close --topic <slug> --reason <code> --note ...` "
        "(or `map topic close --id <slug>`); "
        "legacy DB topic: `map topic migrate --id <uuid> --slug <name>` first"
    ),
    "reopen": (
        "FS topic status lives in map/topics/<slug>/index.md — edit `status` "
        "directly and note the reason in the close note or a new speech; "
        "legacy DB topic: `map topic migrate` first"
    ),
    "archive": (
        "archive FS topics via `map fs archive --topic <slug>` "
        "(validates closed status, git mv, auto-rebuilds archive INDEX; v0.14 M60); "
        "legacy DB topics stay readable via `topic show` (archive flag no longer maintained)"
    ),
    "archive-undo": (
        "restore a FS-archived topic via `map fs archive --topic <slug> --undo` "
        "(v0.14 M60); index consistency is rebuilt automatically"
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
# topic_app
# ---------------------------------------------------------------------------


@topic_app.command("create")
def topic_create(
    title: str = typer.Option(
        ..., "--title", show_default=False, help="Map topic title."
    ),
    project: uuid.UUID | None = typer.Option(
        None, "--project", help="Ignored; create always writes the local workspace map/ folder."
    ),
    project_key: str | None = typer.Option(
        None, "--project-key", help="Ignored; create always writes the local workspace map/ folder."
    ),
    description: str | None = typer.Option(None, "--description"),
    slug: str | None = typer.Option(
        None,
        "--slug",
        help="Folder name under map/topics/. Default: slugify(--title).",
    ),
    participants: str | None = typer.Option(
        None,
        "--participants",
        help="Participant whitelist (comma-separated, e.g. host,participant).",
    ),
    no_sync: bool = typer.Option(False, "--no-sync", help="Skip remote projection sync after the local write"),
) -> None:
    """Create ``map/topics/<slug>/`` + index.md (FS source of truth; no API write)."""
    from cli.commands.fs import write_new_fs_topic
    from cli.fs_projection import maybe_auto_sync

    _ = (project, project_key)
    index = write_new_fs_topic(
        title=title,
        slug=slug,
        description=description or "",
        participants=participants,
    )
    typer.echo(f"Created {index}")
    maybe_auto_sync(no_sync=no_sync)


def _render_topic_table(topics: Any) -> str:
    """Render a list of TopicSummaryRead as a compact table."""
    headers = ["ID", "Title", "Status", "Round", "Comments", "Exps", "Creator", "Created"]
    rows = []
    for t in topics:
        rows.append([
            short_uuid(t.id),
            truncate(t.title, 50),
            enum_value(t.status),
            enum_value(t.discussion_round),
            str(t.comment_count),
            str(t.experiment_count),
            truncate(t.creator_name, 20),
            format_datetime(t.created_at),
        ])
    return render_table(headers, rows)


@topic_app.command("list")
def topic_list(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    status: str | None = typer.Option(None, "--status"),
    creator: str | None = typer.Option(
        None,
        "--creator",
        help="Filter by topic creator. Accepts agent_name (current project) or agent_id UUID; "
        "alias for --creator-agent-id.",
    ),
    creator_agent_id: uuid.UUID | None = typer.Option(
        None,
        "--creator-agent-id",
        help="Filter by creator agent_id UUID. Use --creator for name-or-id shorthand.",
    ),
    q: str | None = typer.Option(None, "--q"),
    page: int = typer.Option(1, "--page", min=1),
    page_size: int = typer.Option(100, "--page-size", min=1, max=100),
    include_archived: bool = typer.Option(False, "--include-archived"),
) -> None:
    """List topics in the current project (local map/ folders merged with API leftovers).

    Defaults to a compact table view. Use ``--format yaml`` or
    ``--format json`` for full structured output (scripts / piping).
    """
    from map_types.enums import TopicStatus

    from cli.main import _resolve_creator_agent_id, _resolve_project, _run

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        st = TopicStatus(status) if status else None
        resolved_creator_id = _resolve_creator_agent_id(c, pid, creator, creator_agent_id)
        api_topics = _list_api_topics_all(
            c,
            pid,
            status=st,
            creator_agent_id=resolved_creator_id,
            q=q,
            include_archived=include_archived,
        )
        local_fs: list[Any] = []
        if _should_scan_local_fs(project, project_key, pid):
            local_fs = _filter_local_summaries(
                _scan_local_fs_summaries(pid),
                status=status,
                creator=creator,
                creator_agent_id=resolved_creator_id,
                q=q,
            )
        merged = _merge_topic_summaries(local_fs, api_topics)
        return _slice_page(merged, page, page_size)

    _run(action, table_renderer=_render_topic_table)


@topic_app.command("show")
def topic_show(
    topic_id: str = typer.Option(..., "--id", help="Topic UUID (DB), FS uuid5 id, or slug."),
    storage: str | None = typer.Option(None, "--storage", help=_STORAGE_HELP),
) -> None:
    from cli.main import _resolve_project, _run

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":
            from map_fs import parse_topic_dir

            from cli.commands.fs import _content_root_name

            workspace = _optional_workspace()
            if workspace is not None:
                parsed = parse_topic_dir(
                    workspace / _content_root_name(workspace) / "topics" / target, workspace
                )
                if parsed is not None:
                    return _fs_topic_to_detail(parsed)
            return c.get_fs_topic(_resolve_project(c, None, None), target)
        return c.get_topic(target)

    _run(action)


@topic_app.command("progress")
def topic_progress() -> None:
    """Per-agent topic work items view (obligation + contextual); same source as todos topic buckets."""
    from cli.main import _run

    _run(lambda c: c.get_topic_progress())


@topic_app.command("resolve")
def topic_resolve(
    topic_id: str = typer.Option(..., "--id", help="Topic UUID (DB), FS uuid5 id, or slug."),
    storage: str | None = typer.Option(None, "--storage", help=_STORAGE_HELP),
    resolve_file: Path = typer.Option(..., "--file"),
) -> None:
    """(Retired v0.13 M58) DB resolve is gone; decisions ride the FS close note."""

    from cli.main import _load_topic_resolve_payload, _run

    payload = _load_topic_resolve_payload(resolve_file)
    _ = payload  # validated then discarded; the retired hint explains the path

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":
            _fs_transition_rejected("resolve", target)
        _db_write_retired("resolve", str(target))

    _run(action)


@topic_app.command("advance-round")
def topic_advance_round(
    topic_id: str = typer.Option(..., "--id", help="Topic UUID (DB), FS uuid5 id, or slug."),
    storage: str | None = typer.Option(None, "--storage", help=_STORAGE_HELP),
    increment_summary: bool = typer.Option(
        True,
        "--increment-summary/--no-increment-summary",
        help="(DB-only, retired v0.13 M58) Increment round_summary_count before advancing.",
    ),
    ack_ids: str | None = typer.Option(
        None,
        "--ack-ids",
        help="(DB-only, retired v0.13 M58) Host: comma-separated participant agent UUIDs already acknowledged.",
    ),
    ack: str | None = typer.Option(
        None,
        "--ack",
        help="(DB-only, retired v0.13 M58) Participant: accept, reject, or dismiss acknowledgement for the current round.",
    ),
    mark_ready: bool = typer.Option(
        False,
        "--ready",
        help="Mark topic as ready for experiment creation instead of advancing to the next round.",
    ),
    waive_ack: bool = typer.Option(
        False,
        "--waive-ack",
        help="Host: waive the participant ack requirement and advance immediately (requires --waive-reason).",
    ),
    waive_reason: str | None = typer.Option(
        None,
        "--waive-reason",
        help="Reason for waiving the ack requirement (required when --waive-ack is set).",
    ),
) -> None:
    from cli.main import _resolve_project, _run

    if ack_ids:
        # --ack-ids is only meaningful for the retired DB path; still parse to
        # give a precise error instead of a generic usage failure.
        [uuid.UUID(item.strip()) for item in ack_ids.split(",") if item.strip()]

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":
            if ack_ids or ack:
                typer.echo(
                    "Error: --ack / --ack-ids are DB-topic options (retired v0.13 M58); "
                    "FS topics advance when round files are present or with --waive-ack "
                    "(see `map fs advance-round`).",
                    err=True,
                )
                raise typer.Exit(2)
            from map_types.schemas.fs import FsAdvanceRoundRequest

            from cli.commands.fs import validated_write_flow

            def validate_call(
                client: MAPClient,
                pid: uuid.UUID,
                evidence,
                base_revision: int | None,
            ):
                return client.fs_validate_advance_round(
                    pid,
                    target,
                    FsAdvanceRoundRequest(
                        waive_ack=waive_ack,
                        waive_reason=waive_reason,
                        mark_ready=mark_ready,
                        base_revision=base_revision,
                        evidence=evidence,
                    ),
                )

            return validated_write_flow(
                c,
                pid=_resolve_project(c, None, None),
                action_name="advance-round",
                topic=target,
                validate_call=validate_call,
            )
        _db_write_retired("advance-round", str(target))

    _run(action)


@topic_app.command("rollback-round")
def topic_rollback_round(
    topic_id: str = typer.Option(..., "--id", help="Topic UUID (DB), FS uuid5 id, or slug."),
    storage: str | None = typer.Option(None, "--storage", help=_STORAGE_HELP),
) -> None:
    """(Retired v0.13 M58) DB rollback is gone; FS rounds are file facts (edit files)."""
    from cli.main import _run

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":
            _fs_transition_rejected("rollback-round", target)
        _db_write_retired("rollback-round", str(target))

    _run(action)


@topic_app.command("comment")
def topic_comment(
    topic_id: str = typer.Option(..., "--id", help="Topic UUID (DB), FS uuid5 id, or slug."),
    storage: str | None = typer.Option(None, "--storage", help=_STORAGE_HELP),
    body: str | None = typer.Option(None, "--body"),
    body_file: Path | None = typer.Option(None, "--file"),
    parent: uuid.UUID | None = typer.Option(None, "--parent"),
    round_summary: bool = typer.Option(
        False,
        "--round-summary",
        help="Mark this comment as a Round Summary (triggers participant ack flow).",
    ),
    file_path: str | None = typer.Option(
        None,
        "--file-path",
        help="MAP slimming: store local MD file path instead of inline body. "
        "Use with --excerpt for list preview.",
    ),
    excerpt: str | None = typer.Option(
        None,
        "--excerpt",
        help="Short excerpt for list views (max 200 chars). Use with --file-path.",
    ),
    no_sync: bool = typer.Option(False, "--no-sync", help="Skip remote projection sync after the local write"),
) -> None:
    from cli.main import _read_text_file, _run

    has_inline = body is not None or body_file is not None
    if not has_inline and file_path is None:
        typer.echo(
            "Error: provide --body, --file, or --file-path", err=True
        )
        raise typer.Exit(2)
    if body is not None and body_file is not None:
        typer.echo("Error: use only one of --body or --file", err=True)
        raise typer.Exit(2)
    content = body if body is not None else (_read_text_file(body_file, kind="comment") if body_file else None)

    # M51：comment 的 FS 路由本地优先（发言 = 纯本地写 round 文件，无需 API）。
    # slug → map/topics/<slug>/ 存在即 FS；uuid → 本地 uuid5 反查命中即 FS
    # （uuid5 命名空间与 DB uuid4 碰撞可忽略）；否则走 DB API。
    def _fs_comment_target() -> str | None:
        if _looks_like_uuid(topic_id):
            return _fs_slug_by_uuid(topic_id)
        from map_fs import parse_topic_dir

        workspace, root = _fs_workspace_and_root()
        t = parse_topic_dir(workspace / root / "topics" / topic_id, workspace)
        return t.slug if t is not None else None

    if storage == "fs":
        target = _fs_comment_target()
        if target is None:
            typer.echo(f"Error: fs topic not found: {topic_id} (see `map fs list`)", err=True)
            raise typer.Exit(1)
        _write_fs_comment(target, content, parent, round_summary, file_path, no_sync=no_sync)
        return
    if storage is None and (slug := _fs_comment_target()) is not None:
        _write_fs_comment(slug, content, parent, round_summary, file_path, no_sync=no_sync)
        return

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":  # pragma: no cover - 本地优先分支已拦截；兜底保持一致
            _write_fs_comment(target, content, parent, round_summary, file_path, exit_after=True, no_sync=no_sync)
            raise typer.Exit(0)
        _db_write_retired("comment", str(target))

    _run(action)


def _write_fs_comment(
    slug: str,
    content: str | None,
    parent: uuid.UUID | None,
    round_summary: bool,
    file_path: str | None,
    *,
    exit_after: bool = False,
    no_sync: bool = False,
) -> None:
    """FS 话题发言 = 写 round<N>-<persona>.md（纯本地，与 map fs comment 同语义）。"""
    if content is None:
        typer.echo(
            "Error: fs topics need --body / --file (content is stored in the round "
            "file); --file-path is a DB-topic reference-only option.",
            err=True,
        )
        raise typer.Exit(2)
    if parent is not None:
        typer.echo(
            "Error: --parent is a DB-topic option; FS threading uses in-file "
            "section references (see file-reference.md).",
            err=True,
        )
        raise typer.Exit(2)
    from map_fs import write_round_comment

    from cli.commands.fs import _content_root_name, _current_round, _persona, _workspace

    workspace = _workspace()
    try:
        path = write_round_comment(
            workspace,
            slug,
            round_number=_current_round(workspace, slug),
            persona=_persona(None),
            body=content,
            is_round_summary=round_summary,
            content_root=_content_root_name(workspace),
        )
    except FileExistsError as err:
        typer.echo(f"Error: {err} (use `map fs comment --force` to overwrite)", err=True)
        raise typer.Exit(1) from err
    except ValueError as err:
        # W1 写路径前置校验：body 自带 frontmatter（--force 不豁免）
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(2) from err
    typer.echo(f"Wrote {path} (fs topic: {slug})")
    from cli.fs_projection import maybe_auto_sync

    maybe_auto_sync(no_sync=no_sync, workspace=workspace)
    if exit_after:
        raise typer.Exit(0)


@topic_app.command("close")
def topic_close(
    topic_id: str = typer.Option(..., "--id", help="Topic UUID (DB), FS uuid5 id, or slug."),
    storage: str | None = typer.Option(None, "--storage", help=_STORAGE_HELP),
    reason: str | None = typer.Option(
        None,
        "--reason",
        help="Short reason code for closing (e.g. 'no_experiment_needed', 'superseded').",
    ),
    note: str | None = typer.Option(
        None,
        "--note",
        help="Longer explanation for why the topic is being closed.",
    ),
) -> None:
    from cli.main import _resolve_project, _run

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":
            from map_types.schemas.fs import FsCloseRequest

            from cli.commands.fs import validated_write_flow

            def validate_call(
                client: MAPClient,
                pid: uuid.UUID,
                evidence,
                base_revision: int | None,
            ):
                return client.fs_validate_close(
                    pid,
                    target,
                    FsCloseRequest(
                        close_reason=reason,
                        close_note=note,
                        base_revision=base_revision,
                        evidence=evidence,
                    ),
                )

            return validated_write_flow(
                c,
                pid=_resolve_project(c, None, None),
                action_name="close",
                topic=target,
                validate_call=validate_call,
            )
        _db_write_retired("close", str(target))

    _run(action)


@topic_app.command("reopen")
def topic_reopen(
    topic_id: str = typer.Option(..., "--id", help="Topic UUID (DB), FS uuid5 id, or slug."),
    storage: str | None = typer.Option(None, "--storage", help=_STORAGE_HELP),
) -> None:
    """(Retired v0.13 M58) DB reopen is gone; FS status lives in index.md."""
    from cli.main import _run

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":
            _fs_transition_rejected("reopen", target)
        _db_write_retired("reopen", str(target))

    _run(action)


@topic_app.command("dismiss")
def topic_dismiss(
    topic_id: str = typer.Option(..., "--id", help="Topic UUID (DB), FS uuid5 id, or slug."),
    storage: str | None = typer.Option(None, "--storage", help=_STORAGE_HELP),
) -> None:
    """Hide an open topic from host todos until new activity (same as Web UI ✕).

    DB topics only; FS targets are a no-op with a notice (FS pending items
    clear by writing round files).
    """
    from cli.main import _run

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":
            _fs_projection_noop("dismiss", target)
        return c.dismiss_topic(target)

    _run(action)


@topic_app.command("read")
def topic_read(
    topic_id: str = typer.Option(..., "--id", help="Topic UUID (DB), FS uuid5 id, or slug."),
    storage: str | None = typer.Option(None, "--storage", help=_STORAGE_HELP),
) -> None:
    """Mark contextual unread changes as seen; obligations still require reply/ack/mention handling.

    DB topics only; FS targets are a no-op with a notice (FS pending items
    clear by writing round files).
    """
    from cli.main import _run

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":
            _fs_projection_noop("read", target)
        return c.mark_topic_read(target)

    _run(action)


@topic_app.command("mark-seen")
def topic_mark_seen(
    topic_id: str = typer.Option(..., "--id", help="Topic UUID (DB), FS uuid5 id, or slug."),
    storage: str | None = typer.Option(None, "--storage", help=_STORAGE_HELP),
) -> None:
    """Alias of topic read: clears contextual unread only, not reply/ack/mention obligations.

    DB topics only; FS targets are a no-op with a notice (FS pending items
    clear by writing round files).
    """
    from cli.main import _run

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":
            _fs_projection_noop("mark-seen", target)
        return c.mark_topic_read(target)

    _run(action)


@topic_app.command(
    "archive",
    epilog="(Retired v0.13 M58) FS archiving = moving map/topics/<slug>/ to map/archive/topics/.",
)
def topic_archive(
    topic_id: uuid.UUID | None = typer.Option(
        None,
        "--id",
        help="Topic UUID (DB only: archived is a DB-record flag; FS topics have no archive concept).",
    ),
    undo: bool = typer.Option(
        False,
        "--undo",
        help="Unarchive instead of archive. Equivalent to --unarchive.",
    ),
    unarchive: bool = typer.Option(
        False,
        "--unarchive",
        help="Alias of --undo: unarchive instead of archive.",
    ),
) -> None:
    """(Retired v0.13 M58) DB archive flag is gone; FS archiving is a file move.

    Legacy DB topics stay readable via ``topic show``; the archived flag is no
    longer maintained from the CLI. FS topics archive by moving the folder to
    ``map/archive/topics/``.
    """
    _db_write_retired("archive-undo" if (undo or unarchive) else "archive")


def _plan_db_to_fs_migration(topic: Any, workspace: Path) -> dict[str, Any]:
    """DB TopicRead → FS 写入计划（纯函数，便于测试）。

    轮次启发式：round summary 评论界定轮次（summary 归属其所在轮），
    其后的评论进入下一轮；index 轮号不低于 topic.discussion_round。
    同人同轮的多条 DB 评论合并进一个 round<N>-<persona>.md（--- 分隔）。
    """
    import yaml

    def persona_of(agent_name: str | None) -> str:
        if not agent_name:
            return "host"
        cfg = workspace / ".map" / "agents.yaml"
        if cfg.is_file():
            try:
                data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                data = {}
            personas = data.get("personas") if isinstance(data, dict) else None
            if isinstance(personas, dict):
                for key, meta in personas.items():
                    if isinstance(meta, dict) and meta.get("agent_name") == agent_name:
                        return str(key)
        return agent_name

    def flatten(nodes: Any, out: list[Any]) -> list[Any]:
        for n in nodes or []:
            out.append(n)
            flatten(getattr(n, "children", None), out)
        return out

    groups: dict[tuple[int, str], dict[str, Any]] = {}
    order: list[tuple[int, str]] = []

    def bucket(rn: int, persona: str) -> dict[str, Any]:
        key = (rn, persona)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"bodies": [], "summary": False, "all_system": True}
            order.append(key)
        return g

    round_number = 1
    seen: set[str] = set()
    for cm in sorted(flatten(topic.comments, []), key=lambda x: (x.created_at, x.comment_seq)):
        persona = persona_of(cm.author_name)
        seen.add(persona)
        g = bucket(round_number, persona)
        body = (cm.body or cm.excerpt or "").strip()
        if cm.file_path:
            body = f"*content: {cm.file_path}*\n\n{body}" if body else f"*content: {cm.file_path}*"
        if body:
            g["bodies"].append(body)
        if cm.is_round_summary:
            g["summary"] = True
            round_number += 1
        if str(enum_value(cm.kind)) != "system":
            g["all_system"] = False

    decision = getattr(topic, "decision", None)
    if decision is not None:
        try:
            dump = yaml.safe_dump(decision.model_dump(mode="json"), allow_unicode=True, sort_keys=False)
        except Exception:
            dump = str(decision)
        bucket(round_number, persona_of(getattr(topic, "creator_name", None)))["bodies"].append(
            f"## Decision\n\n```yaml\n{dump}```"
        )

    files: list[tuple[int, str, str, str, bool]] = []
    max_round = 0
    for rn, persona in order:
        g = groups[(rn, persona)]
        files.append(
            (
                rn,
                persona,
                "\n\n---\n\n".join(g["bodies"]) or "*(no content)*",
                "system" if g["all_system"] else "user",
                g["summary"],
            )
        )
        max_round = max(max_round, rn)
    dr = str(enum_value(topic.discussion_round))
    if dr.startswith("round") and dr[5:].isdigit():
        max_round = max(max_round, int(dr[5:]))
    creator_persona = persona_of(getattr(topic, "creator_name", None))
    return {
        "index": {
            "title": topic.title,
            "creator": creator_persona,
            "description": topic.description or "",
            "status": "closed" if str(enum_value(topic.status)) == "closed" else "open",
            "round_": max_round or 1,
            "participants": sorted(seen | {creator_persona}),
        },
        "files": files,
    }


@topic_app.command("migrate")
def topic_migrate(
    topic_id: uuid.UUID = typer.Option(..., "--id", help="DB topic UUID to migrate."),
    slug: str = typer.Option(..., "--slug", help="Target folder name: map/topics/<slug>/"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List planned writes without touching files or the DB."
    ),
) -> None:
    """Migrate a DB topic to the map/ folder source of truth (one-way, M51).

    FS 完整落盘（index.md + 全部 round 文件）成功后才 archive DB 记录
    （列表默认隐藏，show 仍可见）；中途失败不产生半迁移。
    """
    from cli.main import _run

    _run(lambda c: _execute_db_to_fs_migration(c, topic_id, slug, dry_run=dry_run))


def _execute_db_to_fs_migration(
    c: MAPClient, topic_id: uuid.UUID, slug: str, *, dry_run: bool = False
) -> Any:
    from map_fs import write_round_comment, write_topic_index
    from map_types.schemas import TopicUpdate

    workspace, root = _fs_workspace_and_root()
    target_dir = workspace / root / "topics" / slug
    if target_dir.exists():
        typer.echo(f"Error: target already exists: {target_dir} (pick another --slug)", err=True)
        raise typer.Exit(1)

    topic = c.get_topic(topic_id)
    plan = _plan_db_to_fs_migration(topic, workspace)
    if dry_run:
        idx = plan["index"]
        typer.echo(
            f"[dry-run] write {target_dir / 'index.md'} "
            f"(status={idx['status']}, round={idx['round_']}, participants={','.join(idx['participants'])})"
        )
        for rn, persona, _body, _kind, summary in plan["files"]:
            suffix = " (round summary)" if summary else ""
            typer.echo(f"[dry-run] write {target_dir / f'round{rn}-{persona}.md'}{suffix}")
        typer.echo(f"[dry-run] archive DB topic {topic_id} (archived=true)")
        return None
    index_path = write_topic_index(workspace, slug, content_root=root, **plan["index"])
    typer.echo(f"Wrote {index_path}")
    for rn, persona, body, kind, summary in plan["files"]:
        path = write_round_comment(
            workspace,
            slug,
            round_number=rn,
            persona=persona,
            body=body,
            kind=kind,
            is_round_summary=summary,
            content_root=root,
        )
        typer.echo(f"Wrote {path}")
    updated = c.update_topic(topic_id, TopicUpdate(archived=True))
    typer.echo(f"Archived DB topic {topic_id} (hidden from list; show still works)")
    return updated


# ---------------------------------------------------------------------------
# mention_app
# ---------------------------------------------------------------------------


@mention_app.command("dismiss")
def mention_dismiss(
    mention_id: uuid.UUID = typer.Option(..., "--id", help="Mention UUID from `map todos`."),
) -> None:
    """Dismiss one @mention for the current persona (removes it from `map todos`).

    Idempotent: dismissing an already-dismissed mention returns the same result.
    """
    from cli.main import _run

    _run(lambda c: c.dismiss_mention(mention_id))


@mention_app.command("list")
def mention_list() -> None:
    """List open @mentions for the current persona."""
    from cli.main import _run

    _run(lambda c: c.get_todos().mentions)


@mention_app.command("dismiss-all")
def mention_dismiss_all() -> None:
    """Dismiss all open @mentions for the current persona."""
    from cli.main import _run

    _run(lambda c: c.dismiss_all_mentions())


@mention_app.command("reconcile-stale")
def mention_reconcile_stale() -> None:
    """Admin stub: offline stale mention reconciliation (T1 D5 MVP — not implemented)."""
    typer.echo(
        "mention reconcile-stale: stub only — stale mentions are filtered in "
        "topic-progress/todos projection; use write-path dismiss on comment."
    )


# ---------------------------------------------------------------------------
# todo_app — explicit_only partition clear router
# ---------------------------------------------------------------------------


@todo_app.command("clear")
def todo_clear(
    key: str = typer.Option(..., "--key", help="Work-item idempotency_key or partition id"),
) -> None:
    """Route explicit_only todo partitions to the canonical clear CLI (T1 D7)."""
    from cli.main import _run

    if key.startswith("notification:"):
        notification_id = uuid.UUID(key.split(":", 1)[1])
        _run(lambda c: c.mark_notification_read(notification_id))
        return
    if key.startswith("action_item:"):
        item_id = uuid.UUID(key.split(":", 1)[1])
        _run(lambda c: c.complete_action_item(item_id))
        return
    if key.startswith("my_open_topics:") or key.startswith("topic:"):
        topic_id = uuid.UUID(key.rsplit(":", 1)[-1])
        _run(lambda c: c.dismiss_topic(topic_id))
        return
    if key.startswith("unread_change:"):
        topic_id = uuid.UUID(key.split(":", 2)[1])
        _run(lambda c: c.mark_topic_read(topic_id))
        return
    raise typer.BadParameter(
        f"unsupported todo clear key {key!r}; explicit_only: notification, action_item, "
        "my_open_topics, unread_change"
    )


# ---------------------------------------------------------------------------
# action_item_app — FS 话题执行项(action-items.yaml)（plan v3 I4，A4/A5）
# 纯本地写：读解析 → 变更 → 原子写回 action-items.yaml → maybe_auto_sync。
# owner 按 persona 短名路由到 work 义务（A2），close 门禁校验清零（A3）。
# ---------------------------------------------------------------------------


def _ai_local_topic(workspace: Path, root: str, topic: str) -> Any:
    """定位 FS 话题文件夹；缺失时给出可操作错误（写回前必须存在）。"""
    from map_fs import parse_topic_dir

    parsed = parse_topic_dir(workspace / root / "topics" / topic, workspace)
    if parsed is None:
        typer.echo(f"Error: fs topic not found: {topic}（`map topic action-item` 只作用于 map/ 话题）", err=True)
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
    topic: str = typer.Option(..., "--topic", help="FS 话题 slug"),
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
    topic: str = typer.Option(..., "--topic", help="FS 话题 slug"),
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
    topic: str = typer.Option(..., "--topic", help="FS 话题 slug"),
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
    topic: str = typer.Option(..., "--topic", help="FS 话题 slug"),
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
