"""``map topic show / history / read / mark-seen`` 读视图命令 — T45 拆分自
topic.py（实验 0f271f7e A6 再并入 read / mark-seen 两个读面命令）。

命令体的路由 helpers 直接取自源模块 ``cli.topic_routing``；``_STORAGE_HELP``
装饰期值经 register() 内 call-time 导入宿主取得。宿主 ``topic_app`` 底部
挂载，无循环导入。
"""
from __future__ import annotations

import typer
from map_client.client import MAPClient

from cli import runner  # module ref: test monkeypatch surface (T23)
from cli.runner import _client_ctx
from cli.topic_routing import (
    _fs_projection_noop,
    _fs_slug_by_uuid,
    _fs_topic_to_detail,
    _looks_like_uuid,
    _optional_workspace,
    _resolve_topic_ref,
)


def register(app: typer.Typer) -> None:
    """Register read-view commands on the host ``topic_app``."""
    # Decoration-time value (help=_STORAGE_HELP): import while the fully
    # loaded host runs register() at its module bottom.
    from cli.commands.topic import _STORAGE_HELP

    @app.command("read")
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

    @app.command("mark-seen")
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

    @app.command("show")
    def topic_show(
        topic_id: str = typer.Option(
            ..., "--id", "--topic", help="Topic UUID (DB), folder uuid5 id, or slug."
        ),
        storage: str | None = typer.Option(None, "--storage", help=_STORAGE_HELP),
        full: bool = typer.Option(False, "--full", help="Print full comment bodies for local folder topics."),
    ) -> None:
        if storage != "db":
            workspace = _optional_workspace()
            if workspace is not None:
                from map_fs import parse_topic_dir

                from cli.commands.fs import _content_root_name, fs_show

                root = _content_root_name(workspace)
                slug = topic_id
                if _looks_like_uuid(topic_id):
                    found = _fs_slug_by_uuid(topic_id)
                    if found:
                        slug = found
                parsed = parse_topic_dir(workspace / root / "topics" / slug, workspace)
                archived = (workspace / root / "archive" / "topics" / slug).is_dir()
                if parsed is not None or archived:
                    fs_show(topic=slug, full=full)
                    return

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
                return c.get_fs_topic(runner._resolve_project(c, None, None), target)
            return c.get_topic(target)

        runner._run(action)


    @app.command("history")
    def topic_history(
        topic_id: str = typer.Option(..., "--id", help="Topic UUID (DB), folder uuid5 id, or slug."),
        kind: str | None = typer.Option(
            None,
            "--kind",
            help="Optional AuditLog.action filter applied after merge.",
        ),
        limit: int = typer.Option(
            50,
            "--limit",
            min=1,
            max=200,
            help="Max merged rows after sorting (≤200).",
        ),
    ) -> None:
        """Topic-dimension audit timeline (ops-visibility-batch C2).

        ``GET /audit?target_type=topic`` plus audit events of experiments whose
        ``topic_id`` matches, sorted by time descending. Host and participant
        share the same GET /audit permission check — no new ACL.
        """

        from map_client.exceptions import MAPHTTPError, MAPPermissionError
        from map_fs import topic_id_for_slug

        from cli.audit_target import (
            ResolvedTarget,
            emit_audit_timeline,
            fetch_topic_history,
            resolve_audit_target,
        )

        try:
            with _client_ctx() as client:
                # Prefer slug/uuid via shared resolver; if it lands on an
                # experiment (same string), still force topic semantics via
                # _resolve_topic_ref so `topic history --id <exp-slug>` 报错清晰.
                kind_ref, target = _resolve_topic_ref(client, topic_id, None)
                if kind_ref == "fs":
                    resolved = ResolvedTarget(
                        "topic", topic_id_for_slug(str(target)), str(target)
                    )
                else:
                    resolved = resolve_audit_target(client, str(target))
                    if resolved.target_type != "topic":
                        typer.echo(
                            f"Error: '{topic_id}' resolved to an experiment; "
                            "topic history needs a topic slug/uuid "
                            "(use `map audit list --target` for experiments)",
                            err=True,
                        )
                        raise typer.Exit(2)
                items = fetch_topic_history(
                    client, resolved, limit=limit, kind=kind
                )
        except MAPPermissionError as exc:
            typer.echo(f"Error {exc.status_code}: {exc.detail}", err=True)
            raise typer.Exit(1) from exc
        except MAPHTTPError as exc:
            suffix = ""
            if exc.error_code:
                suffix += f" [error_code={exc.error_code}]"
            if exc.hint:
                suffix += f"\nHint: {exc.hint}"
            typer.echo(f"Error {exc.status_code}: {exc.detail}{suffix}", err=True)
            raise typer.Exit(1) from exc
        emit_audit_timeline(
            items,
            empty_message=f"No audit events for topic '{resolved.label}'.",
        )
