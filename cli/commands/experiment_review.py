"""``map experiment review|plan`` sub-apps — T33 extraction from experiment.py.

Owns ``experiment review add/list/withdraw/resolve-item`` and
``experiment plan revise/validate``. Shared lifecycle helpers
(``_rid`` / ``_run_lifecycle`` / ``_ID_HELP``) stay in
``cli.commands.experiment`` — that module is the test monkeypatch surface
(e.g. ``monkeypatch.setattr("cli.commands.experiment._rid", ...)``) — so
command bodies import them at call time (T23).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import typer
import yaml
from map_client.client import MAPClient
from map_types.schemas import PlanRevise, ReviewCreate

from cli import runner  # module ref: test monkeypatch surface (T23)
from cli.io_helpers import _read_text_file
from cli.runner import _print_json

review_app = typer.Typer(help="Review commands")
plan_app = typer.Typer(help="Plan commands")

# ``_ID_HELP`` is read at decoration time, so it is imported once here —
# below the app definitions on purpose: importing the host module lets it
# finish loading (its bottom block re-imports the apps defined above), which
# keeps both import orders (via cli.commands.experiment or direct) working.
from cli.commands.experiment import _ID_HELP  # noqa: E402


@review_app.command("add")
def review_add(
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
    review_file: Path = typer.Option(..., "--review"),
) -> None:
    from cli.commands.experiment import _run_lifecycle

    raw = yaml.safe_load(_read_text_file(review_file, kind="review"))
    payload = ReviewCreate.model_validate(raw)
    dumped = payload.model_dump(mode="json")
    _run_lifecycle(
        experiment_id,
        call=lambda c, rid, _before: c.create_review(rid, payload),
        review_payload=dumped if isinstance(dumped, dict) else {"review": dumped},
        review_filename="plan-review.yaml",
    )


@plan_app.command("revise")
def plan_revise(
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
    plan_file: Path = typer.Option(..., "--plan-file"),
    note: str | None = typer.Option(None, "--note"),
    addressed_item: list[uuid.UUID] = typer.Option(
        [],
        "--addressed-item",
        help="Unreasonable review item UUID to mark addressed (repeatable).",
    ),
    breaking_audit: bool = typer.Option(
        False,
        "--breaking-audit",
        help="架构级修订显式标记（实验 bd9b21f6 A1）：running 相位打回 pending_review 重评，"
        "complete 被真拦截直至重评通过；change_note 必须写明相对上一版改了什么/为什么（缺失即拒绝）",
    ),
) -> None:
    from cli.commands.experiment import _run_lifecycle

    payload = PlanRevise(
        content_md=_read_text_file(plan_file, kind="plan"),
        change_note=note,
        addressed_item_ids=list(addressed_item),
        breaking_audit=breaking_audit,
    )
    _run_lifecycle(
        experiment_id,
        call=lambda c, rid, _before: c.revise_plan(rid, payload),
        plan_content=payload.content_md,
    )


@plan_app.command("validate")
def plan_validate(
    plan_file: Path = typer.Option(..., "--plan-file"),
    strict: bool = typer.Option(
        False,
        "--strict/--no-strict",
        help="Exit 1 when warnings are present (default: exit 0, warnings only).",
    ),
) -> None:
    """Local plan frontmatter lint (no API call).

    Reads the plan file, parses YAML frontmatter, and lists any
    missing/invalid required fields (``title`` / ``acceptance`` /
    ``evidence_keys`` / ``dependencies``). Soft-validates by default
    (always exits 0 unless the file is unreadable); use ``--strict`` to
    exit 1 when warnings are present, so ``experiment create`` scripts
    can gate on lint outcome.
    """
    # T33: pure helper moved from cli.main to cli.subcommand_format.
    from cli.subcommand_format import _project_cli_default_format
    from server.services.plan_marker_service import validate_plan_frontmatter

    content = _read_text_file(plan_file, kind="plan")
    result = validate_plan_frontmatter(content)

    payload = {
        "valid": result.valid,
        "fields_present": list(result.fields_present),
        "warnings": [
            {
                "code": w.code,
                "field": w.field,
                "detail": w.detail,
            }
            for w in result.warnings
        ],
        "frontmatter": result.frontmatter,
    }

    fmt = _project_cli_default_format(None)
    if fmt == "json":
        _print_json(payload)
    else:
        typer.echo(
            f"Plan lint: {'PASS' if result.valid else 'FAIL'} "
            f"(fields_present={len(result.fields_present)}, warnings={len(result.warnings)})"
        )
        if result.frontmatter is not None:
            typer.echo(f"frontmatter: {result.frontmatter}")
        for w in result.warnings:
            field_label = w.field or "-"
            detail = f" ({w.detail})" if w.detail else ""
            typer.echo(f"  - [{w.code}] {field_label}{detail}")

    if strict and result.warnings:
        raise typer.Exit(1)


@plan_app.command("materialize")
def plan_materialize(
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
    force: bool = typer.Option(
        False,
        "--force",
        help="Replace an existing plan.md only when it differs from the current DB plan.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate the current DB plan and report the target path without writing it.",
    ),
) -> None:
    """Materialize the current DB-backed plan to its experiment ``plan.md``.

    This is an explicit repair path for experiments created with
    ``--plan-file`` (inline content) before a filesystem plan artifact was
    requested.  The target is derived from the existing, projected experiment
    folder; callers cannot choose an arbitrary filesystem destination.
    """
    from map_fs import validate_experiment_index_file

    from cli.commands.experiment import _rid
    from cli.experiment_fs import (
        find_fs_experiment,
        materialize_experiment_plan,
    )
    from cli.project_context import current_context
    from server.services.plan_marker_service import validate_plan_frontmatter

    def action(c: MAPClient):
        exp = c.get_experiment(_rid(c, experiment_id))
        me = c.get_me()
        if exp.creator_agent_id != me.id:
            typer.echo("Error: only the experiment creator may materialize its plan", err=True)
            raise typer.Exit(1)
        if exp.plan_file_path:
            typer.echo(
                "Error: this experiment already uses --plan-file-path; read its referenced file instead",
                err=True,
            )
            raise typer.Exit(2)
        if exp.current_plan is None or not exp.current_plan.content_md.strip():
            typer.echo("Error: current experiment plan content is unavailable", err=True)
            raise typer.Exit(1)

        content = exp.current_plan.content_md
        lint = validate_plan_frontmatter(content)
        if not lint.valid:
            typer.echo("Error: current DB plan fails frontmatter validation; refuse to materialize", err=True)
            raise typer.Exit(1)

        ctx = current_context()
        fs = find_fs_experiment(ctx.workspace_root, ref=exp.id)
        if fs is None:
            typer.echo(
                "Error: experiment has no projected map/experiments/<slug>/index.md; refuse to infer a destination",
                err=True,
            )
            raise typer.Exit(1)
        validate_experiment_index_file(ctx.workspace_root, fs.slug, content_root=ctx.content_root)
        target = ctx.workspace_root / ctx.content_root / "experiments" / fs.slug / "plan.md"
        if dry_run:
            return {
                "experiment_id": str(exp.id),
                "plan_path": str(target.relative_to(ctx.workspace_root)),
                "wrote": False,
                "dry_run": True,
            }

        path, wrote = materialize_experiment_plan(
            ctx.workspace_root,
            fs.slug,
            content,
            force=force,
            content_root=ctx.content_root,
        )
        return {
            "experiment_id": str(exp.id),
            "plan_path": str(path.relative_to(ctx.workspace_root)),
            "wrote": wrote,
            "dry_run": False,
        }

    runner._run(action, experiment_id=experiment_id)


@review_app.command("list")
def review_list(
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
    # N=2 过渡期默认 true (experiment 18f1d8f6 I1(c))；N=2 release 后切到 False。
    # 见 plan 当前_plan_version 的 history 默认展示策略。
    include_archived: bool = typer.Option(
        True,
        "--include-archived/--no-include-archived",
        help="Include archived reviews. Default true during N=2 transition; flips to false at N=2 release.",
    ),
    plan_version: int | None = typer.Option(
        None,
        "--plan-version",
        help="Filter by exact plan_version. Combine with --include-archived to inspect historical review chains.",
    ),
) -> None:
    from cli.commands.experiment import _rid

    def action(c: MAPClient):
        return c.list_reviews(
            _rid(c, experiment_id),
            include_archived=include_archived,
            plan_version=plan_version,
        )

    runner._run(action, experiment_id=experiment_id)


@review_app.command("withdraw")
def review_withdraw(
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
    review_id: uuid.UUID = typer.Option(..., "--review-id"),
) -> None:
    from cli.commands.experiment import _rid

    runner._run(lambda c: c.withdraw_review(_rid(c, experiment_id), review_id), experiment_id=experiment_id)


@review_app.command("resolve-item")
def review_resolve_item(
    item_id: uuid.UUID = typer.Option(..., "--id"),
    status: str = typer.Option(
        "resolved",
        "--status",
        help="Target terminal status for this review item.",
    ),
) -> None:
    """Resolve a single review item.

    ``--status`` accepts ``resolved`` (accept the item) or ``rebutted``
    (host pushes back on the item while keeping the experiment moving).
    Defaults to ``resolved`` so existing scripts that omit ``--status``
    keep their old behaviour — the migration is opt-in.
    """
    from map_types.enums import ReviewItemStatus

    runner._run(lambda c: c.update_review_item(item_id, ReviewItemStatus(status)))
