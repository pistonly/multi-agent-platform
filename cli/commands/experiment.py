"""``map experiment ...`` sub-app — cli/main.py split.

Owns experiment lifecycle commands plus nested ``lock`` / ``review`` / ``plan``.
Command bodies lazy-import ``cli.main`` helpers to break the
``cli.main ↔ cli.commands.*`` import cycle.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import typer
import yaml
from map_client.client import MAPClient
from map_client.exceptions import MAPNotFoundError
from map_sdk.evidence import (
    EVIDENCE_METADATA_KEYS,
    metadata_has_completion_evidence,
)
from map_types.schemas import (
    ExperimentComplete,
    ExperimentCreate,
    ExperimentLogCreate,
    ExperimentResultDecision,
    PlanInput,
    PlanRevise,
    ReviewCreate,
)

from map_types.enums import ExperimentMode

from cli.table_render import enum_value, format_datetime, render_table, short_uuid, truncate

experiment_app = typer.Typer(help="Experiment commands", rich_markup_mode=None)
lock_app = typer.Typer(help="Experiment execution lock commands (per-project).")
review_app = typer.Typer(help="Review commands")
plan_app = typer.Typer(help="Plan commands")
experiment_app.add_typer(lock_app, name="lock")
experiment_app.add_typer(review_app, name="review")
experiment_app.add_typer(plan_app, name="plan")


@experiment_app.command("create")
def experiment_create(
    title: str = typer.Option(..., "--title"),
    plan_file: Path = typer.Option(..., "--plan-file"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    description: str | None = typer.Option(None, "--description"),
    submit_for_review: bool = typer.Option(False, "--submit-for-review"),
    topic_id: uuid.UUID | None = typer.Option(None, "--topic-id"),
    mode: str = typer.Option(
        "standard",
        "--mode",
        help=(
            "Experiment lifecycle mode: 'standard' (default, with reviewer gates) "
            "or 'direct' (host specifies plan, participant executes, no reviewer). "
            "direct mode: draft → running → done."
        ),
    ),
    force_lint_bypass: bool = typer.Option(
        False,
        "--force-lint-bypass",
        help="Skip the local plan frontmatter lint pre-check (server still enforces it).",
    ),
) -> None:
    from cli.main import _read_text_file, _resolve_project, _run  # lazy: avoid cycle
    content = _read_text_file(plan_file, kind="plan")
    # Local plan frontmatter lint pre-check (a764abf6 I1.(c)). The server
    # has its own hard validator (``assert_plan_frontmatter_ok``), but a
    # local gate saves a round trip and gives a clearer error message
    # when the author simply forgot the YAML block.
    if not force_lint_bypass:
        from server.services.plan_marker_service import validate_plan_frontmatter

        result = validate_plan_frontmatter(content)
        missing_fields = [
            w.field for w in result.warnings if w.code == "PLAN_MARKER_MISSING_FIELD"
        ]
        if missing_fields or any(
            w.code == "PLAN_MARKER_FRONT_MATTER_MISSING"
            for w in result.warnings
        ):
            keys = ", ".join(sorted(set(missing_fields))) if missing_fields else "(no frontmatter)"
            typer.echo(
                f"Error: plan frontmatter lint failed ({keys}). "
                f"Run `map experiment plan validate --plan-file {plan_file}` "
                f"for details, or re-run with --force-lint-bypass to skip the local check.",
                err=True,
            )
            raise typer.Exit(2)

    # Validate mode value early so the user gets a clear error.
    try:
        exp_mode = ExperimentMode(mode)
    except ValueError:
        typer.echo(
            f"Error: invalid mode '{mode}'. Use 'standard' or 'direct'.",
            err=True,
        )
        raise typer.Exit(2)

    payload = ExperimentCreate(
        title=title,
        description=description,
        plan=PlanInput(content_md=content),
        submit_for_review=submit_for_review,
        topic_id=topic_id,
        mode=exp_mode,
    )

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        return c.create_experiment(pid, payload)

    _run(action)


def _render_experiment_table(experiments: Any) -> str:
    """Render a list of ExperimentSummaryRead as a compact table."""
    headers = ["ID", "Title", "Phase", "Mode", "Plan v", "Logs", "Topic", "Updated"]
    rows = []
    for e in experiments:
        rows.append([
            short_uuid(e.id),
            truncate(e.title, 50),
            enum_value(e.phase),
            enum_value(getattr(e, "mode", "standard")),
            f"v{e.current_plan_version}",
            str(getattr(e, "log_count", 0)),
            short_uuid(e.topic_id) if e.topic_id else "-",
            format_datetime(e.updated_at),
        ])
    return render_table(headers, rows)


@experiment_app.command("list")
def experiment_list(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    phase: str | None = typer.Option(None, "--phase"),
    creator_agent_id: uuid.UUID | None = typer.Option(None, "--creator-agent-id"),
    q: str | None = typer.Option(None, "--q"),
    page: int = typer.Option(1, "--page", min=1),
    page_size: int = typer.Option(100, "--page-size", min=1, max=100),
    include_archived: bool = typer.Option(False, "--include-archived"),
) -> None:
    """List experiments in the current project.

    Defaults to a compact table view. Use ``--format yaml`` or
    ``--format json`` for full structured output (scripts / piping).
    """
    from map_types.enums import ExperimentPhase

    from cli.main import _resolve_project, _run  # lazy: avoid cycle

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        phase_filter = ExperimentPhase(phase) if phase else None
        return c.list_experiments(
            pid,
            phase=phase_filter,
            creator_agent_id=creator_agent_id,
            q=q,
            page=page,
            page_size=page_size,
            include_archived=include_archived,
        )

    _run(action, table_renderer=_render_experiment_table)


@experiment_app.command("submit-review")
def experiment_submit_review(experiment_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    from cli.main import _run  # lazy: avoid cycle
    _run(lambda c: c.submit_for_review(experiment_id), experiment_id=experiment_id)


@experiment_app.command("approve")
def experiment_approve(experiment_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    from cli.main import _run  # lazy: avoid cycle
    _run(lambda c: c.approve_experiment(experiment_id), experiment_id=experiment_id)


@experiment_app.command("start")
def experiment_start(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    executor: str | None = typer.Option(
        None,
        "--executor",
        help=(
            "Delegate execution to another agent (name or UUID). The designated "
            "executor becomes the sole non-admin caller allowed to ``complete``. "
            "Omit to self-execute (host runs the experiment)."
        ),
    ),
) -> None:
    """Start experiment execution (approved → running, or draft → running in direct mode).

    Migration 042 adds optional executor delegation: pass ``--executor``
    with an agent name or UUID to designate who may call ``complete``.
    The host retains all other lifecycle gates (cancel / withdraw / etc).

    v0.10: in ``direct`` mode, the experiment goes from ``draft`` directly
    to ``running`` (skipping review/approved). Use ``--executor participant``
    to delegate execution to the participant persona.
    """
    from cli.main import _resolve_executor_agent_id, _resolve_project, _run  # lazy: avoid cycle

    def action(c: MAPClient):
        executor_agent_id: uuid.UUID | None = None
        if executor is not None:
            pid = _resolve_project(c, None, None)
            executor_agent_id = _resolve_executor_agent_id(c, pid, executor)
        return c.start_experiment(experiment_id, executor_agent_id=executor_agent_id)

    _run(action, experiment_id=experiment_id)


@experiment_app.command("pre-complete")
def experiment_pre_complete(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    metadata_file: Path | None = typer.Option(
        None,
        "--metadata",
        help="YAML evidence file with keys such as alembic_current, api_health, pytest_summary, image_digest.",
    ),
) -> None:
    """Validate local completion evidence before `experiment complete`.

    This is intentionally local and side-effect free: it checks that the
    experiment exists and that the supplied metadata carries at least one
    deploy/test evidence field.
    """
    from cli.main import _read_yaml_file, _run  # lazy: avoid cycle

    metadata = _read_yaml_file(metadata_file)
    if not metadata_has_completion_evidence(metadata):
        keys = ", ".join(sorted(EVIDENCE_METADATA_KEYS))
        typer.echo(
            "Error: missing completion evidence metadata. "
            f"Accepted keys include: {keys}.",
            err=True,
        )
        raise typer.Exit(2)

    def _action(client: MAPClient):
        exp = client.get_experiment(experiment_id)
        return {
            "experiment_id": str(exp.id),
            "phase": exp.phase,
            "current_plan_version": exp.current_plan_version,
            "evidence_keys": sorted(str(key) for key in metadata) if isinstance(metadata, dict) else [],
            "ok": True,
        }

    _run(_action)


@experiment_app.command("complete")
def experiment_complete(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    summary: str = typer.Option(..., "--summary"),
    log_file: Path = typer.Option(..., "--file"),
    metadata_file: Path | None = typer.Option(None, "--metadata"),
    allow_missing_evidence: bool = typer.Option(
        False,
        "--allow-missing-evidence",
        help="Bypass metadata evidence check for non-deployment experiments.",
    ),
    schema: bool = typer.Option(
        False,
        "--schema",
        help="cli-ux PR1: print the --metadata YAML template (with field hints) and exit. "
        "Use this to discover accepted keys without grepping the SDK.",
    ),
) -> None:
    """Submit experiment result for reviewer approval (running → result_review).

    The log body must follow the 4-段 template contract (summary / 实施 log /
    风险 / acceptance). Soft validation runs server-side; any
    ``template_validation.warnings`` are surfaced to stderr (one
    ``[WARN] template: <code>`` line each) and to the stdout YAML/JSON
    payload under ``template_validation``. Warnings never block the
    transition — add a follow-up log to address them.
    """
    from cli.main import (  # lazy: avoid cycle
        _load_complete_metadata,
        _print_complete_metadata_schema_and_exit,
        _read_text_file,
        _run,
    )
    if schema:
        _print_complete_metadata_schema_and_exit()
    metadata = _load_complete_metadata(
        metadata_file,
        allow_missing_evidence=allow_missing_evidence,
    )
    payload = ExperimentComplete(
        summary=summary,
        content_md=_read_text_file(log_file, kind="log"),
        metadata=metadata,
    )
    _run(lambda c: c.complete_experiment(experiment_id, payload), experiment_id=experiment_id)


@experiment_app.command("accept-result")
def experiment_accept_result(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    summary: str = typer.Option(..., "--summary"),
    log_file: Path = typer.Option(..., "--file"),
    metadata_file: Path | None = typer.Option(None, "--metadata"),
    review_verdict_file: Path | None = typer.Option(
        None,
        "--review-verdict-file",
        help=(
            "Optional structured verdict file (YAML). When omitted, the call "
            "is treated as legacy free-text (pre_schema_accept_result='true'). "
            "When provided, server validates review_id/item_id ownership and "
            "records verdict breakdown in log metadata."
        ),
    ),
    schema: bool = typer.Option(
        False,
        "--schema",
        help="cli-ux PR1: print the --review-verdict-file YAML template (with field hints) and exit. "
        "Use this to discover the verdict schema without grepping the SDK.",
    ),
) -> None:
    from cli.main import (  # lazy: avoid cycle
        _load_review_verdict_file,
        _print_review_verdict_schema_and_exit,
        _read_text_file,
        _read_yaml_file,
        _run,
    )
    if schema:
        _print_review_verdict_schema_and_exit()
    metadata = _read_yaml_file(metadata_file)
    verdict_file = _load_review_verdict_file(review_verdict_file)
    payload = ExperimentResultDecision(
        summary=summary,
        content_md=_read_text_file(log_file, kind="log"),
        metadata=metadata,
        verdict_file=verdict_file,
    )
    _run(lambda c: c.accept_experiment_result(experiment_id, payload), experiment_id=experiment_id)


@experiment_app.command("reject-result")
def experiment_reject_result(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    summary: str = typer.Option(..., "--summary"),
    log_file: Path = typer.Option(..., "--file"),
    metadata_file: Path | None = typer.Option(None, "--metadata"),
    review_verdict_file: Path | None = typer.Option(
        None,
        "--review-verdict-file",
        help=(
            "Optional structured verdict file (YAML). When omitted, the call "
            "is treated as legacy free-text (pre_schema_accept_result='true'). "
            "When provided, server validates review_id/item_id ownership and "
            "records verdict breakdown in log metadata."
        ),
    ),
    schema: bool = typer.Option(
        False,
        "--schema",
        help="cli-ux PR1: print the --review-verdict-file YAML template (with field hints) and exit. "
        "Use this to discover the verdict schema without grepping the SDK.",
    ),
) -> None:
    from cli.main import (  # lazy: avoid cycle
        _load_review_verdict_file,
        _print_review_verdict_schema_and_exit,
        _read_text_file,
        _read_yaml_file,
        _run,
    )
    if schema:
        _print_review_verdict_schema_and_exit()
    metadata = _read_yaml_file(metadata_file)
    verdict_file = _load_review_verdict_file(review_verdict_file)
    payload = ExperimentResultDecision(
        summary=summary,
        content_md=_read_text_file(log_file, kind="log"),
        metadata=metadata,
        verdict_file=verdict_file,
    )
    _run(lambda c: c.reject_experiment_result(experiment_id, payload), experiment_id=experiment_id)


@experiment_app.command("log")
def experiment_log(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    summary: str = typer.Option(..., "--summary"),
    log_file: Path = typer.Option(..., "--file"),
    metadata_file: Path | None = typer.Option(None, "--metadata"),
    force_skip_similarity: bool = typer.Option(
        False,
        "--force-skip-similarity",
        help=(
            "b72d0542 I1.b(2)(e): acknowledge the soft content-similarity "
            "warning when the new log body is >= 70%% similar to a prior "
            "log. The warning is suppressed in stdout and a "
            "``log.force_skip`` audit row is written instead."
        ),
    ),
) -> None:
    """Append an execution log entry to an experiment.

    Output (8ac93d4e I1.d): stdout is the ``LogCreateResponse`` wrapper
    (yaml/json, top-level ``log`` + ``validation``). Soft plan evidence_keys
    warnings appear as ``validation.warnings`` in stdout. When the plan's
    frontmatter existed but its YAML failed to parse, the CLI additionally
    emits ``[WARN] plan evidence_keys 解析失败: <msg>`` on stderr so the
    host notices the malformed plan; the log itself is still saved.

    b72d0542 I1.b(2)(e): when the server detects content similarity
    >= threshold against a prior log on the same experiment, the
    ``similarity_warning`` block is added to the stdout payload AND a
    ``[WARN] similarity: HIGH_CONTENT_SIMILARITY score=X >= threshold=Y``
    line is emitted on stderr. Pass ``--force-skip-similarity`` to
    suppress the warning and instead write a ``log.force_skip`` audit row.
    """
    from cli.main import _read_text_file, _read_yaml_file, _run  # lazy: avoid cycle
    metadata = _read_yaml_file(metadata_file)
    payload = ExperimentLogCreate(
        summary=summary,
        content_md=_read_text_file(log_file, kind="log"),
        metadata=metadata,
        force_skip_similarity=force_skip_similarity,
    )
    _run(lambda c: c.create_log(experiment_id, payload), experiment_id=experiment_id)


@experiment_app.command("logs")
def experiment_logs(experiment_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    from cli.main import _run  # lazy: avoid cycle
    _run(lambda c: c.list_logs(experiment_id), experiment_id=experiment_id)


@experiment_app.command("status")
def experiment_status(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    persona_compare: bool = typer.Option(
        False,
        "--persona-compare",
        help="0db51e10 I1(5a): diff the per-actor view of this experiment across "
        "multiple personas (default: host+reviewer+participant). Each persona's "
        "view is fetched independently via its own token.",
    ),
    raw: bool = typer.Option(
        False,
        "--raw",
        help="0db51e10 I1(5a): with --persona-compare, print each persona's full "
        "snapshot side-by-side (YAML) instead of the diff table.",
    ),
    compare_personas: str | None = typer.Option(
        None,
        "--for-personas",
        help="0db51e10 I1(5a): with --persona-compare, comma-separated persona "
        "names to compare (e.g. 'host,reviewer'); default = all known personas.",
    ),
) -> None:
    """Show one experiment's phase, actions, blocked_on, and (per-actor) capabilities."""
    from cli.main import _persona_compare_view, _run  # lazy: avoid cycle
    if persona_compare:
        _run(
            lambda c: _persona_compare_view(
                c,
                experiment_id,
                personas=(
                    [p.strip() for p in compare_personas.split(",") if p.strip()]
                    if compare_personas
                    else None
                ),
                raw=raw,
            ),
            experiment_id=experiment_id,
        )
        return

    def _action(client: MAPClient):
        result = client.get_experiment(experiment_id)
        typer.echo(f"actions: {list(result.actions)}")
        typer.echo(f"blocked_on: {result.blocked_on}")
        typer.echo(f"phase_owner: {getattr(result, 'phase_owner', 'host')}")
        typer.echo(f"informational_only: {getattr(result, 'informational_only', False)}")
        if result.blocked_on == "open_unreasonable_item" or "plan_revise" in result.actions:
            typer.echo(
                "obligation: revise plan for open unreasonable items "
                "(see pending_plan_revisions in map todos / map work)"
            )
        elif result.blocked_on == "awaiting_review_for_current_plan_version":
            typer.echo(
                "obligation: reviewer must experiment review add for the current "
                f"plan version (v{result.current_plan_version}); host cannot approve until then"
            )
        elif result.blocked_on == "awaiting_non_creator_review":
            typer.echo(
                "obligation: reviewer must submit the first experiment review "
                "(experiment review add)"
            )
        # f873c287 I1(d): unified "host blocked, waiting on {phase_owner}"
        # catch-all for blocked experiments whose decision authority is
        # held by a non-host persona and have NO blocked_on-specific
        # actionable hint above (e.g. result_review /
        # awaiting_result_approval). When a specific obligation branch
        # already matched, the phase_owner line printed above carries
        # the equivalent "who's holding this" signal — no need to
        # duplicate.
        elif (
            getattr(result, "blocked_on", None)
            and getattr(result, "phase_owner", None)
            and result.phase_owner.value != "host"
            and not result.actions
        ):
            typer.echo(
                f"obligation: blocked, waiting on {result.phase_owner.value} "
                f"(blocked_on={result.blocked_on})"
            )
        return result

    _run(_action)


@experiment_app.command("show")
def experiment_show(
    experiment_id: uuid.UUID | None = typer.Option(None, "--id", help="Experiment UUID."),
) -> None:
    """Show one experiment (including archived) by UUID."""
    from cli.main import _require_option_uuid, _run  # lazy: avoid cycle
    experiment_id = _require_option_uuid(experiment_id)
    _run(lambda c: c.get_experiment(experiment_id), experiment_id=experiment_id)


@experiment_app.command(
    "archive",
    epilog="Use --undo or --unarchive to restore an archived experiment.",
)
def experiment_archive(
    experiment_id: uuid.UUID | None = typer.Option(None, "--id", help="Experiment UUID."),
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
    """Archive (or unarchive) an experiment.

    Thin wrapper around ``PATCH /experiments/{id}`` with ``archived=true``
    (or ``false`` when ``--undo``/``--unarchive`` is set). Archive hides the
    experiment from ``experiment list`` by default but ``experiment show``
    still returns it including ``archived_at``. Archive is reversible —
    re-run with ``--undo`` to restore.

    Examples:

        # Archive
        map --persona host experiment archive --id <uuid>

        # Unarchive (two equivalent spellings)
        map --persona host experiment archive --id <uuid> --undo
        map --persona host experiment archive --id <uuid> --unarchive
    """
    from cli.main import _require_option_uuid, _run  # lazy: avoid cycle
    experiment_id = _require_option_uuid(experiment_id)
    from map_types.schemas import ExperimentUpdate

    payload = ExperimentUpdate(archived=not (undo or unarchive))
    object_kind = "experiment"

    def action(c: MAPClient):
        try:
            return c.update_experiment(experiment_id, payload)
        except MAPNotFoundError as exc:
            typer.echo(
                f"Error: {object_kind} {experiment_id} not found",
                err=True,
            )
            raise typer.Exit(1) from exc

    _run(action)


@lock_app.command("acquire")
def experiment_lock_acquire(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    ttl: int = typer.Option(1800, "--ttl", min=1, help="Lock TTL in seconds."),
) -> None:
    from cli.main import _run  # lazy: avoid cycle
    _run(lambda c: c.acquire_experiment_lock(experiment_id, ttl_seconds=ttl), experiment_id=experiment_id)


@lock_app.command("release")
def experiment_lock_release(experiment_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    from cli.main import _run  # lazy: avoid cycle
    _run(lambda c: c.release_experiment_lock(experiment_id), experiment_id=experiment_id)


@lock_app.command("force-release")
def experiment_lock_force_release(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    reason: str = typer.Option(..., "--reason"),
    actor: str | None = typer.Option(None, "--actor"),
) -> None:
    from cli.main import _run  # lazy: avoid cycle
    _run(lambda c: c.force_release_experiment_lock(experiment_id, reason=reason, actor=actor), experiment_id=experiment_id)


@lock_app.command("skip")
def experiment_lock_skip(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    next_attempt_at: str = typer.Option(..., "--next-attempt-at"),
) -> None:
    from cli.main import _run  # lazy: avoid cycle
    _run(lambda c: c.record_experiment_lock_skip(experiment_id, next_attempt_at=next_attempt_at), experiment_id=experiment_id)


@lock_app.command("scan-stalled")
def experiment_lock_scan_stalled() -> None:
    """Scan running experiment locks and emit no-progress notifications."""
    from cli.main import _run  # lazy: avoid cycle

    _run(lambda c: c.scan_stalled_experiment_locks())


@review_app.command("add")
def review_add(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    review_file: Path = typer.Option(..., "--review"),
) -> None:
    from cli.main import _read_text_file, _run  # lazy: avoid cycle
    raw = yaml.safe_load(_read_text_file(review_file, kind="review"))
    payload = ReviewCreate.model_validate(raw)
    _run(lambda c: c.create_review(experiment_id, payload), experiment_id=experiment_id)


@plan_app.command("revise")
def plan_revise(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    plan_file: Path = typer.Option(..., "--plan-file"),
    note: str | None = typer.Option(None, "--note"),
    addressed_item: list[uuid.UUID] = typer.Option(
        [],
        "--addressed-item",
        help="Unreasonable review item UUID to mark addressed (repeatable).",
    ),
) -> None:
    from cli.main import _read_text_file, _run  # lazy: avoid cycle
    payload = PlanRevise(
        content_md=_read_text_file(plan_file, kind="plan"),
        change_note=note,
        addressed_item_ids=list(addressed_item),
    )
    _run(lambda c: c.revise_plan(experiment_id, payload), experiment_id=experiment_id)


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
    from cli.main import _print_json, _project_cli_default_format, _read_text_file  # lazy: avoid cycle
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


@review_app.command("list")
def review_list(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
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
    from cli.main import _run  # lazy: avoid cycle
    def action(c: MAPClient):
        return c.list_reviews(
            experiment_id,
            include_archived=include_archived,
            plan_version=plan_version,
        )

    _run(action)


@review_app.command("withdraw")
def review_withdraw(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    review_id: uuid.UUID = typer.Option(..., "--review-id"),
) -> None:
    from cli.main import _run  # lazy: avoid cycle
    _run(lambda c: c.withdraw_review(experiment_id, review_id))


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

    from cli.main import _run  # lazy: avoid cycle

    _run(lambda c: c.update_review_item(item_id, ReviewItemStatus(status)))


@experiment_app.command("comment")
def experiment_comment(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    anchor_type: str = typer.Option(..., "--anchor-type"),
    anchor_id: uuid.UUID = typer.Option(..., "--anchor-id"),
    body: str | None = typer.Option(None, "--body"),
    body_file: Path | None = typer.Option(None, "--file", help="Read body from a file (avoids shell-quoting issues)."),
    parent: uuid.UUID | None = typer.Option(None, "--parent"),
) -> None:
    from map_types.enums import CommentAnchorType
    from map_types.schemas import CommentCreate

    from cli.main import _read_text_file, _run  # lazy: avoid cycle

    if body is None and body_file is None:
        typer.echo("Error: either --body or --file is required", err=True)
        raise typer.Exit(2)
    if body is not None and body_file is not None:
        typer.echo("Error: use only one of --body or --file", err=True)
        raise typer.Exit(2)
    content = body if body is not None else _read_text_file(body_file, kind="comment")
    payload = CommentCreate(
        anchor_type=CommentAnchorType(anchor_type),
        anchor_id=anchor_id,
        parent_id=parent,
        body=content,
    )
    _run(lambda c: c.create_comment(experiment_id, payload))
