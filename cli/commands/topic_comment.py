"""``map topic comment`` — extraction from ``cli/commands/topic.py``.

Module-size-cap split: the comment command (option matrix + FS routing +
round-file writer) is the largest single command in the topic sub-app.
Moved verbatim; ``topic.py`` registers it with
``topic_app.command("comment")(topic_comment)`` to keep the ``map topic
comment`` CLI path unchanged. No ``commands_topic`` monkeypatch surface
is affected (``_write_fs_comment`` has no external references; tests
drive the command through the CLI runner).
"""
from __future__ import annotations

import uuid
from pathlib import Path

import typer
from map_client.client import MAPClient

from cli import runner  # module ref: test monkeypatch surface (T23)
from cli.io_helpers import _read_piped_text, _read_text_file
from cli.topic_routing import (
    STORAGE_HELP,
    _db_write_retired,
    _fs_slug_by_uuid,
    _looks_like_uuid,
    _optional_fs_workspace_and_root,
    _resolve_topic_ref,
    comment_immutable_ready_hint,
    db_uuid_write_preflight,
)


def topic_comment(
    topic_id: str = typer.Option(
        ..., "--topic", "--id", help="Topic UUID (DB), folder uuid5 id, or slug."
    ),
    storage: str | None = typer.Option(None, "--storage", help=STORAGE_HELP),
    body: str | None = typer.Option(
        None,
        "--body",
        help="短文本正文；多行 Markdown 可省略此项并从 stdin 管道输入，或使用 --file。",
    ),
    body_file: Path | None = typer.Option(
        None,
        "--file",
        help="从 MD 文件读取正文；也可省略内容参数并从 stdin 管道输入。",
    ),
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
        help="仅允许替换已存在的 Round Summary 文件；普通发言始终 immutable；不豁免 frontmatter 前置校验",
    ),
    append: bool = typer.Option(
        False,
        "--append",
        help=(
            "在本轮已存在的发言文件末尾追加 Addendum 小节（只增不改，原正文不可篡改）；"
            "front-matter 记 updated_at。与 --force / --round-summary 互斥。"
        ),
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

    if body is not None and body_file is not None:
        typer.echo("Error: use only one of --body or --file", err=True)
        raise typer.Exit(2)
    if append and (force or round_summary):
        typer.echo(
            "Error: --append cannot be combined with --force or --round-summary "
            "(append only adds an Addendum section to an existing comment)",
            err=True,
        )
        raise typer.Exit(2)
    if body is not None:
        content = body
    elif body_file is not None:
        content = _read_text_file(body_file, kind="comment")
    elif file_path is None:
        content = _read_piped_text(kind="comment")
        if content is None:
            typer.echo(
                "Error: provide --body, --file, or pipe Markdown on stdin "
                "(or use --file-path)",
                err=True,
            )
            raise typer.Exit(2)
    else:
        content = None

    # M51：comment 的 FS 路由本地优先（发言 = 纯本地写 round 文件，无需 API）。
    # slug → map/topics/<slug>/ 存在即 FS；uuid → 本地 uuid5 反查命中即 FS
    # （uuid5 命名空间与 DB uuid4 碰撞可忽略）；否则走 DB API。
    def _fs_comment_target() -> str | None:
        if _looks_like_uuid(topic_id):
            return _fs_slug_by_uuid(topic_id)
        resolved = _optional_fs_workspace_and_root()
        if resolved is None:
            # 本地 workspace 不可用＝FS 未命中；落 DB/退役引导路径，
            # root 错误不应抢在定性之前（exit 2 优先于 exit 1）。
            return None
        workspace, root = resolved
        from map_fs import parse_topic_dir

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
            append=append,
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
            append=append,
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
                append=append,
            )
            raise typer.Exit(0)
        _db_write_retired("comment", str(target))

    db_uuid_write_preflight("comment", topic_id, storage)
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
    append: bool = False,
) -> None:
    """话题发言 = 写普通 round 文件或独立 Summary 文件（纯本地）。

    ``append=True`` 时改为在已存在的本轮发言文件末尾追加 Addendum 小节
    （原正文不可篡改，front-matter 记 ``updated_at``）。
    """
    if content is None:
        typer.echo(
            "Error: folder topics need --body, --file, or piped stdin (content is "
            "stored in the round file); --file-path is a DB-topic reference-only "
            "option.",
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
    from map_fs import append_round_comment, write_round_comment

    from cli.commands.fs import _content_root_name, _current_round, _persona, _workspace

    workspace = _workspace()
    if force:
        typer.echo(
            "Warning: --force only replaces an existing round summary file "
            "(round<N>-summary-<persona>.md); plain comments stay immutable. "
            "Commit first if you need the old content auditable.",
            err=True,
        )
    resolved_round = (
        round_number if round_number is not None else _current_round(workspace, slug)
    )
    try:
        if append:
            path = append_round_comment(
                workspace,
                slug,
                round_number=resolved_round,
                persona=_persona(persona),
                body=content,
                content_root=_content_root_name(workspace),
            )
        else:
            path = write_round_comment(
                workspace,
                slug,
                round_number=resolved_round,
                persona=_persona(persona),
                body=content,
                is_round_summary=round_summary,
                content_root=_content_root_name(workspace),
                overwrite=force,
            )
    except FileExistsError as err:
        hint = comment_immutable_ready_hint(
            workspace, slug, _content_root_name(workspace)
        )
        typer.echo(
            f"Error: {err}{hint}\n"
            "Hint: 想在本轮发言后补充内容，用 `map topic comment --append` "
            "（只增不改，原发言保留）。",
            err=True,
        )
        raise typer.Exit(1) from err
    except FileNotFoundError as err:
        typer.echo(
            f"Error: {err}\nHint: 本轮还没有你的发言文件；如需新发言请去掉 --append。",
            err=True,
        )
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
