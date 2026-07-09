"""``map audit ...`` sub-app — arch experiment 0519e2a3 PR5.

Admin-only audit log surface — wraps ``GET /api/v1/audit``.

Bypasses the shared ``_run`` wrapper on purpose: the API returns
``(rows, total)`` which we render as a header line + YAML rows rather
than a raw tuple. The shared wrapper would YAML-dump the tuple as
``[rows, total]`` which is the wrong shape for a greppable timeline.
"""
from __future__ import annotations

import uuid

import typer
from map_client.exceptions import MAPHTTPError

audit_app = typer.Typer(help="Audit log commands (admin)")


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
        help="Filter to a single experiment (matches payload_json.experiment_id).",
    ),
    page: int = typer.Option(1, "--page", min=1),
    page_size: int = typer.Option(50, "--page-size", min=1, max=200),
) -> None:
    """List global audit log entries (admin only).

    Examples::

        map audit list
        map audit list --kind review_item.mutation
        map audit list --kind review_item.mutation --experiment <uuid>
    """
    from cli.main import _admin_client_ctx, _print_json

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
