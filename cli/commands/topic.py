"""``map topic ...`` + ``map mention ...`` + ``map todo ...`` sub-apps — arch PR6.

Three sub-apps that share the "topic work items" domain:

* ``map topic ...`` — unified topic facade (create / comment / advance-round /
  close / archive / list / show / progress / dismiss / read / mark-seen /
  migrate). Writes go to ``map/topics/<slug>/``; ``list``/``show`` merge local
  folders with leftover DB topics from the API. v0.13 M58: DB write paths
  retired — resolve / rollback-round / reopen (and the DB branches of
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
from typing import Any

import typer
from map_client.client import MAPClient

from cli import runner  # module ref: test monkeypatch surface (T23)
from cli.commands.action_item import action_item_app  # noqa: E402
from cli.io_helpers import _read_text_file  # noqa: E402
from cli.runner import (  # noqa: E402
    _load_topic_resolve_payload,
    _resolve_creator_agent_id,
)
from cli.table_render import enum_value, format_datetime, render_table, short_uuid, truncate

# T33: --id routing / local-FS scan helpers moved to cli.topic_routing; the
# command bodies below import them by name (tests pin them at the source).
from cli.topic_routing import (  # noqa: E402
    _db_write_retired,
    _filter_local_summaries,
    _fs_projection_noop,
    _fs_slug_by_uuid,
    _fs_transition_rejected,
    _fs_workspace_and_root,
    _list_api_topics_all,
    _looks_like_uuid,
    _merge_topic_summaries,
    _resolve_topic_ref,
    _scan_local_fs_summaries,
    _should_scan_local_fs,
    _slice_page,
)

# Retired DB writes stay callable but hidden from `map topic --help`.
# Daily writes (create/comment/advance-round/close/archive) are visible.

topic_app = typer.Typer(
    help=(
        "Topics: create/comment/advance-round/close/archive write map/topics/<slug>/; "
        "list/show/progress/history merge local folders with leftover DB topics. "
        "migrate/dismiss/read/mark-seen stay available. "
        "DB write paths (resolve/rollback-round/reopen) are retired."
    ),
    rich_markup_mode=None,
)
mention_app = typer.Typer(help="Mention todo commands")
todo_app = typer.Typer(help="Todo partition clear routing (explicit_only buckets)")
# T33: the action-item sub-app lives in cli.commands.action_item; keep the
# `map topic action-item ...` CLI path by registering it here.
topic_app.add_typer(action_item_app, name="action-item")

_STORAGE_HELP = (
    "Route --id explicitly: 'fs' (map/ folder topic) or 'db' (platform DB). "
    "Default auto-routing: uuid -> DB first, then FS uuid5; slug -> FS first, then DB slug. "
    "v0.13 M58: explicit 'db' on write commands is rejected with guidance (DB write paths retired)."
)


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
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite existing index.md; preserve comments, created_at, experiments.",
    ),
    no_sync: bool = typer.Option(False, "--no-sync", help="Skip remote projection sync after the local write"),
) -> None:
    """Create map/topics/<slug>/ + index.md.

    默认拒绝 slug 冲突：``map/topics/<slug>/index.md`` 已存在 → exit 1 +
    stderr 含 "already exists"。``--force`` 显式覆盖：保留评论文件 +
    created_at 不变 + experiments 关联列表按 append 合并；created_at 偷渡
    （如未来 CLI 暴露 --created-at）由 parser 层 ValueError 拦截。
    """
    from cli.commands.fs import write_new_fs_topic
    from cli.fs_projection import maybe_auto_sync

    _ = (project, project_key)
    try:
        index = write_new_fs_topic(
            title=title,
            slug=slug,
            description=description or "",
            participants=participants,
            force=force,
        )
    except FileExistsError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None
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

    # local plane：FS 是唯一事实源，list 纯本地（不建客户端、不合并 API）。
    # --include-archived / --creator-agent-id 为 server 概念，local plane 不适用。
    from cli.commands import fs as fs_cli

    if fs_cli.is_local_plane():
        from map_client.project_config import find_map_dir, load_project_map_config

        def local_action(_c):
            cfg = load_project_map_config(map_dir=find_map_dir(None))
            if not cfg.project_id:
                typer.echo(
                    "Error: plane: local requires project_id in .map/config.yaml",
                    err=True,
                )
                raise typer.Exit(1)
            pid = uuid.UUID(cfg.project_id)
            rows = _filter_local_summaries(
                _scan_local_fs_summaries(pid),
                status=status,
                creator=creator,
                creator_agent_id=None,
                q=q,
            )
            return _slice_page(rows, page, page_size)

        runner._run(
            local_action,
            table_renderer=_render_topic_table,
            client_ctx=runner._null_client_ctx(),
        )
        return

    def action(c: MAPClient):
        pid = runner._resolve_project(c, project, project_key)
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

    runner._run(action, table_renderer=_render_topic_table)


@topic_app.command("progress")
def topic_progress() -> None:
    """Per-agent topic work items view (obligation + contextual); same source as todos topic buckets."""

    runner._run(lambda c: c.get_topic_progress())


@topic_app.command("resolve", hidden=True)
def topic_resolve(
    topic_id: str = typer.Option(..., "--id", help="Topic UUID (DB), folder uuid5 id, or slug."),
    storage: str | None = typer.Option(None, "--storage", help=_STORAGE_HELP),
    resolve_file: Path = typer.Option(..., "--file"),
) -> None:
    """(Retired v0.13 M58) DB resolve is gone; decisions ride the FS close note."""


    payload = _load_topic_resolve_payload(resolve_file)
    _ = payload  # validated then discarded; the retired hint explains the path

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":
            _fs_transition_rejected("resolve", target)
        _db_write_retired("resolve", str(target))

    runner._run(action)


@topic_app.command("advance-round")
def topic_advance_round(
    topic_id: str = typer.Option(
        ..., "--id", "--topic", help="Topic UUID (DB), folder uuid5 id, or slug."
    ),
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

    if ack_ids:
        # --ack-ids is only meaningful for the retired DB path; still parse to
        # give a precise error instead of a generic usage failure.
        [uuid.UUID(item.strip()) for item in ack_ids.split(",") if item.strip()]

    # local plane（plane: local）：零 server 验证型写（共享 map_fs.validation 门禁）。
    from cli.commands import fs as fs_cli

    if fs_cli.is_local_plane():
        from map_fs import validation as fs_validation

        slug = fs_cli.local_topic_slug(topic_id)
        actor = fs_cli.local_actor_persona()
        fs_cli.local_validated_write_flow(
            action_name="advance-round",
            topic=slug,
            actor_persona=actor,
            validate_call=lambda t: fs_validation.validate_advance_round(
                t,
                actor=actor,
                waive_ack=waive_ack,
                waive_reason=waive_reason,
                mark_ready=mark_ready,
            ),
        )
        return

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":
            if ack_ids or ack:
                typer.echo(
                    "Error: --ack / --ack-ids are DB-topic options (retired v0.13 M58); "
                    "FS topics advance when round files are present or with --waive-ack "
                    "(see `map topic advance-round`).",
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
                pid=runner._resolve_project(c, None, None),
                action_name="advance-round",
                topic=target,
                validate_call=validate_call,
            )
        _db_write_retired("advance-round", str(target))

    runner._run(action)


@topic_app.command("rollback-round", hidden=True)
def topic_rollback_round(
    topic_id: str = typer.Option(..., "--id", help="Topic UUID (DB), folder uuid5 id, or slug."),
    storage: str | None = typer.Option(None, "--storage", help=_STORAGE_HELP),
) -> None:
    """(Retired v0.13 M58) DB rollback is gone; FS rounds are file facts (edit files)."""

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":
            _fs_transition_rejected("rollback-round", target)
        _db_write_retired("rollback-round", str(target))

    runner._run(action)


@topic_app.command("comment")
def topic_comment(
    topic_id: str = typer.Option(
        ..., "--topic", "--id", help="Topic UUID (DB), folder uuid5 id, or slug."
    ),
    storage: str | None = typer.Option(None, "--storage", help=_STORAGE_HELP),
    body: str | None = typer.Option(None, "--body"),
    body_file: Path | None = typer.Option(None, "--file"),
    parent: uuid.UUID | None = typer.Option(None, "--parent"),
    persona: str | None = typer.Option(None, "--persona"),
    round_number: int | None = typer.Option(None, "--round", help="默认取话题当前轮次"),
    round_summary: bool = typer.Option(
        False,
        "--round-summary",
        help="Write an independent round<N>-summary-<persona>.md Round Summary.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="覆盖已有评论文件（破坏 immutable 约定）；不豁免 frontmatter 前置校验",
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
            typer.echo(f"Error: topic not found: {topic_id} (see `map topic list`)", err=True)
            raise typer.Exit(1)
        _write_fs_comment(
            target,
            content,
            parent,
            round_summary,
            file_path,
            no_sync=no_sync,
            persona=persona,
            round_number=round_number,
            force=force,
        )
        return
    if storage is None and (slug := _fs_comment_target()) is not None:
        _write_fs_comment(
            slug,
            content,
            parent,
            round_summary,
            file_path,
            no_sync=no_sync,
            persona=persona,
            round_number=round_number,
            force=force,
        )
        return

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":  # pragma: no cover - 本地优先分支已拦截；兜底保持一致
            _write_fs_comment(
                target,
                content,
                parent,
                round_summary,
                file_path,
                exit_after=True,
                no_sync=no_sync,
                persona=persona,
                round_number=round_number,
                force=force,
            )
            raise typer.Exit(0)
        _db_write_retired("comment", str(target))

    runner._run(action)


def _write_fs_comment(
    slug: str,
    content: str | None,
    parent: uuid.UUID | None,
    round_summary: bool,
    file_path: str | None,
    *,
    exit_after: bool = False,
    no_sync: bool = False,
    persona: str | None = None,
    round_number: int | None = None,
    force: bool = False,
) -> None:
    """话题发言 = 写普通 round 文件或独立 Summary 文件（纯本地）。"""
    if content is None:
        typer.echo(
            "Error: folder topics need --body / --file (content is stored in the round "
            "file); --file-path is a DB-topic reference-only option.",
            err=True,
        )
        raise typer.Exit(2)
    if parent is not None:
        typer.echo(
            "Error: --parent is a DB-topic option; folder threading uses in-file "
            "section references (see file-reference.md).",
            err=True,
        )
        raise typer.Exit(2)
    from map_fs import write_round_comment

    from cli.commands.fs import _content_root_name, _current_round, _persona, _workspace

    workspace = _workspace()
    if force:
        typer.echo(
            "Warning: --force overwrites an existing comment file (breaks the "
            "immutable convention). Commit first if you need the old content auditable.",
            err=True,
        )
    try:
        path = write_round_comment(
            workspace,
            slug,
            round_number=(
                round_number if round_number is not None else _current_round(workspace, slug)
            ),
            persona=_persona(persona),
            body=content,
            is_round_summary=round_summary,
            content_root=_content_root_name(workspace),
            overwrite=force,
        )
    except FileExistsError as err:
        typer.echo(f"Error: {err} (use `map topic comment --force` to overwrite)", err=True)
        raise typer.Exit(1) from err
    except ValueError as err:
        # W1 写路径前置校验：body 自带 frontmatter（--force 不豁免）
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(2) from err
    typer.echo(f"Wrote {path}")
    from cli.fs_projection import maybe_auto_sync

    maybe_auto_sync(no_sync=no_sync, workspace=workspace)
    if exit_after:
        raise typer.Exit(0)


@topic_app.command("close")
def topic_close(
    topic_id: str = typer.Option(
        ..., "--id", "--topic", help="Topic UUID (DB), folder uuid5 id, or slug."
    ),
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

    # local plane（plane: local）：零 server 验证型写（共享 map_fs.validation 门禁）。
    from cli.commands import fs as fs_cli

    if fs_cli.is_local_plane():
        from map_fs import validation as fs_validation

        slug = fs_cli.local_topic_slug(topic_id)
        actor = fs_cli.local_actor_persona()
        fs_cli.local_validated_write_flow(
            action_name="close",
            topic=slug,
            actor_persona=actor,
            validate_call=lambda t: fs_validation.validate_close(
                t,
                actor=actor,
                close_reason=reason,
                close_note=note,
            ),
        )
        return

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
                pid=runner._resolve_project(c, None, None),
                action_name="close",
                topic=target,
                validate_call=validate_call,
            )
        _db_write_retired("close", str(target))

    runner._run(action)


@topic_app.command("reopen", hidden=True)
def topic_reopen(
    topic_id: str = typer.Option(..., "--id", help="Topic UUID (DB), folder uuid5 id, or slug."),
    storage: str | None = typer.Option(None, "--storage", help=_STORAGE_HELP),
) -> None:
    """(Retired v0.13 M58) DB reopen is gone; FS status lives in index.md."""

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":
            _fs_transition_rejected("reopen", target)
        _db_write_retired("reopen", str(target))

    runner._run(action)


@topic_app.command("dismiss")
def topic_dismiss(
    topic_id: str = typer.Option(..., "--id", help="Topic UUID (DB), folder uuid5 id, or slug."),
    storage: str | None = typer.Option(None, "--storage", help=_STORAGE_HELP),
) -> None:
    """Hide an open topic from host todos until new activity (same as Web UI ✕).

    DB topics only; FS targets are a no-op with a notice (FS pending items
    clear by writing round files).
    """

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":
            _fs_projection_noop("dismiss", target)
        return c.dismiss_topic(target)

    runner._run(action)


@topic_app.command("read")
def topic_read(
    topic_id: str = typer.Option(..., "--id", help="Topic UUID (DB), folder uuid5 id, or slug."),
    storage: str | None = typer.Option(None, "--storage", help=_STORAGE_HELP),
) -> None:
    """Mark contextual unread changes as seen; obligations still require reply/ack/mention handling.

    DB topics only; FS targets are a no-op with a notice (FS pending items
    clear by writing round files).
    """

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":
            _fs_projection_noop("read", target)
        return c.mark_topic_read(target)

    runner._run(action)


@topic_app.command("mark-seen")
def topic_mark_seen(
    topic_id: str = typer.Option(..., "--id", help="Topic UUID (DB), folder uuid5 id, or slug."),
    storage: str | None = typer.Option(None, "--storage", help=_STORAGE_HELP),
) -> None:
    """Alias of topic read: clears contextual unread only, not reply/ack/mention obligations.

    DB topics only; FS targets are a no-op with a notice (FS pending items
    clear by writing round files).
    """

    def action(c: MAPClient):
        kind, target = _resolve_topic_ref(c, topic_id, storage)
        if kind == "fs":
            _fs_projection_noop("mark-seen", target)
        return c.mark_topic_read(target)

    runner._run(action)


@topic_app.command("archive")
def topic_archive(
    topic: str = typer.Option(..., "--topic", "--id", help="话题 slug（--id 为别名）"),
    undo: bool = typer.Option(
        False, "--undo", help="还原归档话题（archive 目录移回 map/topics/）"
    ),
    unarchive: bool = typer.Option(
        False, "--unarchive", help="Alias of --undo: unarchive instead of archive."
    ),
) -> None:
    """归档话题：校验已 closed 后 git mv 到 map/archive/topics/，并重建索引。"""
    from cli.commands.fs import fs_archive

    fs_archive(topic=topic, undo=undo or unarchive)


@topic_app.command("init")
def topic_init() -> None:
    """创建内容根目录结构：map/topics 与 map/experiments。"""
    from cli.commands.fs import fs_init

    fs_init()


@topic_app.command("anomalies")
def topic_anomalies(
    format: str = typer.Option(
        "table", "--format", help="Output format: table | yaml | json."
    ),
) -> None:
    """离线扫描全部话题 round 文件的 frontmatter anomaly（只报告，不改写）。"""
    from cli.commands.fs import fs_anomalies

    fs_anomalies(format=format)


@topic_app.command("work")
def topic_work(
    persona: str | None = typer.Option(None, "--persona"),
) -> None:
    """离线推导 persona 待办（纯文件存在性，不调 API）。"""
    from cli.commands.fs import fs_work

    fs_work(persona=persona)


# ---------------------------------------------------------------------------
# T45: read-view（show/history）与 migrate 域（migrate/archive-index/
# migrate-from-docs）命令块拆至 topic_view / topic_migrate；``mention`` /
# ``todo`` 子 app 拆至 mention.py / todo.py（cli.main 直接挂载）。本模块
# 保留 topic_app 主体与共享 helpers。底部挂载使两种 import 顺序均可解析。
from cli.commands.topic_migrate import register as _register_migrate  # noqa: E402
from cli.commands.topic_view import register as _register_view  # noqa: E402

_register_view(topic_app)
_register_migrate(topic_app)
