"""``map topic ...`` + ``map mention ...`` + ``map todo ...`` sub-apps — arch PR6.

Three sub-apps that share the "topic work items" domain:

* ``map topic ...`` — topic reads & FS-routed writes (list / show / progress /
  comment / advance-round / close / dismiss / read / mark-seen / migrate).
  v0.13 M58: DB write paths retired — create / resolve / rollback-round /
  reopen / archive (and the DB branches of comment / advance-round / close)
  reject with guidance instead of writing the platform DB.
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
        typer.echo(f"Error: fs topic not found: {ref} (see `map fs list`)", err=True)
        raise typer.Exit(1)
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
        typer.echo(f"Error: topic not found (DB API and map/ folders): {ref}", err=True)
        raise typer.Exit(1)
    slug = fs_hit()
    if slug is not None:
        return ("fs", slug)
    tid = db_hit()
    if tid is not None:
        return ("db", tid)
    typer.echo(
        f"Error: topic not found: {ref} (no map/topics/{ref}/ folder and no DB slug "
        "match; see `map fs list` / `map topic list`)",
        err=True,
    )
    raise typer.Exit(1)


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


# v0.13 M58：DB 话题写路径整体退役。退役面 = create / resolve / rollback-round /
# reopen / archive 五命令全量 + comment / advance-round / close 三命令的 DB 分支
#（含显式 --storage db）。读路径（show/list/progress）与 dismiss/read/mark-seen/
# migrate 不受影响。一律引导性错误（exit 2），不静默成功。
_DB_WRITE_RETIRED_HINTS: dict[str, str] = {
    "create": (
        "create FS topics instead: "
        "`map fs topic-create --title ... --slug <name> --participants <a,b>`"
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
        "FS archiving is a file move: "
        "mv map/topics/<slug>/ map/archive/topics/ ; legacy DB topics stay "
        "readable via `topic show` (archive flag no longer maintained)"
    ),
    "archive-undo": (
        "restore a FS-archived topic by moving the folder back: "
        "mv map/archive/topics/<slug>/ map/topics/"
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
    title: str = typer.Option(..., "--title"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    description: str | None = typer.Option(None, "--description"),
    slug: str | None = typer.Option(
        None,
        "--slug",
        help="Human-readable identifier for file path convention (e.g. 'map-slimming').",
    ),
) -> None:
    """(Retired v0.13 M58) DB topic creation is gone; topics are created as map/ folders."""
    _ = (title, project, project_key, description, slug)  # accepted for clear errors
    _db_write_retired("create")


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
    """List topics in the current project.

    Defaults to a compact table view. Use ``--format yaml`` or
    ``--format json`` for full structured output (scripts / piping).
    """
    from map_types.enums import TopicStatus

    from cli.main import _resolve_creator_agent_id, _resolve_project, _run

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        st = TopicStatus(status) if status else None
        resolved_creator_id = _resolve_creator_agent_id(c, pid, creator, creator_agent_id)
        return c.list_topics(
            pid,
            status=st,
            creator_agent_id=resolved_creator_id,
            q=q,
            page=page,
            page_size=page_size,
            include_archived=include_archived,
        )

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

            return c.fs_advance_round(
                _resolve_project(c, None, None),
                target,
                FsAdvanceRoundRequest(
                    waive_ack=waive_ack, waive_reason=waive_reason, mark_ready=mark_ready
                ),
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
        _write_fs_comment(target, content, parent, round_summary, file_path)
        return
    if storage is None and (slug := _fs_comment_target()) is not None:
        _write_fs_comment(slug, content, parent, round_summary, file_path)
        return

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":  # pragma: no cover - 本地优先分支已拦截；兜底保持一致
            _write_fs_comment(target, content, parent, round_summary, file_path, exit_after=True)
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
    typer.echo(f"Wrote {path} (fs topic: {slug})")
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

            return c.fs_close_topic(
                _resolve_project(c, None, None),
                target,
                FsCloseRequest(close_reason=reason, close_note=note),
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
