"""``map audit ...`` sub-app — arch experiment 0519e2a3 PR5 + 4770ea76 I2.

两条入口：
* 无 ``--target``：admin 全局 ``GET /admin/audit``（C4 行为不变）
* 有 ``--target``：普通 agent ``GET /audit``（C1，权限走 ensure_audit_target_access）
"""
from __future__ import annotations

import uuid

import typer
from map_client.exceptions import MAPHTTPError, MAPPermissionError

audit_app = typer.Typer(help="Audit log commands")


@audit_app.command("list")
def audit_list(
    kind: str | None = typer.Option(
        None,
        "--kind",
        help="Filter by AuditLog.action (e.g. review_item.mutation).",
    ),
    experiment_id: uuid.UUID | None = typer.Option(
        None,
        "--experiment",
        help="Admin global path only: filter payload_json.experiment_id.",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        help=(
            "Topic slug/uuid5 or experiment slug/uuid/shortid. "
            "Uses GET /audit (non-admin). Mutually exclusive with --experiment."
        ),
    ),
    page: int = typer.Option(1, "--page", min=1),
    page_size: int = typer.Option(50, "--page-size", min=1, max=200),
    limit: int = typer.Option(
        50,
        "--limit",
        min=1,
        max=200,
        help="Max rows for --target path (GET /audit limit, ≤200).",
    ),
) -> None:
    """List audit log entries.

    Without ``--target`` this is the admin-only global log. With ``--target``
    any project agent can read the timeline for one topic or experiment.

    Examples::

        map audit list
        map audit list --kind review_item.mutation --experiment <uuid>
        map audit list --target ops-visibility-batch
        map audit list --target 4770ea76 --kind experiment.completed
    """
    from cli.main import _admin_client_ctx, _client_ctx, _print_json

    if target and experiment_id is not None:
        typer.echo(
            "Error: --target and --experiment cannot be combined; "
            "--experiment is the admin global filter, --target is the "
            "non-admin slug/uuid entry",
            err=True,
        )
        raise typer.Exit(2)

    if target:
        from cli.audit_target import emit_audit_timeline, fetch_target_audit, resolve_audit_target

        try:
            with _client_ctx() as client:
                resolved = resolve_audit_target(client, target)
                items = fetch_target_audit(
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
            empty_message=(
                f"No audit events for {resolved.target_type} '{resolved.label}'."
            ),
        )
        return

    try:
        with _admin_client_ctx() as client:
            items, total = client.list_audit_global(
                page=page,
                page_size=page_size,
                kind=kind,
                experiment_id=experiment_id,
            )
    except MAPHTTPError as exc:
        suffix = ""
        error_code = getattr(exc, "error_code", None)
        hint = getattr(exc, "hint", None)
        if error_code:
            suffix += f" [error_code={error_code}]"
        if hint:
            suffix += f"\nHint: {hint}"
        typer.echo(f"Error {exc.status_code}: {exc.detail}{suffix}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(
        f"# audit_log total={total} returned={len(items)} "
        f"kind={kind or '*'} experiment={experiment_id or '*'}"
    )
    _print_json(items)
