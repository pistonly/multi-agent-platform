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
from map_types.enums import ExperimentMode
from map_types.schemas import (
    ExperimentComplete,
    ExperimentCreate,
    ExperimentLogCreate,
    ExperimentResultDecision,
    PlanInput,
    PlanRevise,
    ReviewCreate,
)

from cli.shortid import resolve_ref
from cli.table_render import enum_value, format_datetime, render_table, short_uuid, truncate

experiment_app = typer.Typer(help="Experiment commands", rich_markup_mode=None)
lock_app = typer.Typer(help="Experiment execution lock commands (per-project).")
review_app = typer.Typer(help="Review commands")
plan_app = typer.Typer(help="Plan commands")
experiment_app.add_typer(lock_app, name="lock")
experiment_app.add_typer(review_app, name="review")
experiment_app.add_typer(plan_app, name="plan")

_ID_HELP = (
    "Experiment UUID, uuid5(experiment:<slug>), directory slug, or >=8-hex-digit prefix. "
    "uuid → DB first (404 then FS uuid5); slug → FS first."
)


def _rid(client: MAPClient, raw: str | uuid.UUID) -> uuid.UUID:
    """Resolve a ``--id`` value (full UUID, short prefix, FS slug, or uuid5).

    Short prefixes (>= 8 hex digits) hit the DB layer via
    ``list_experiments_page(id_prefix=...)`` — ``CAST(id AS CHAR) LIKE
    '<prefix>%'`` (plan v2 r1) — including archived experiments so
    ``show``/``archive`` resolve them too.

    M1 A6: slug → FS ``map/experiments/<slug>/`` first; uuid → DB first,
    404 后由 ``_load_experiment`` 反查 FS uuid5 / projection_id.
    """
    from cli.experiment_fs import looks_like_hex_prefix, projection_id_for_fs_ref
    from cli.main import _resolve_project  # lazy: avoid cycle
    from cli.shortid import normalize_uuid_like

    text = str(raw).strip()
    if normalize_uuid_like(text) is None and not looks_like_hex_prefix(text):
        mapped = projection_id_for_fs_ref(text)
        if mapped is not None:
            return mapped

    def matcher(prefix: str) -> list[tuple[uuid.UUID, str]]:
        project_id = _resolve_project(client, None, None)
        items, _total = client.list_experiments_page(
            project_id, id_prefix=prefix, page_size=50, include_archived=True
        )
        return [(e.id, e.title or "") for e in items]

    return resolve_ref(raw, kind="experiment", matcher=matcher)


def _load_experiment(client: MAPClient, raw: str | uuid.UUID):
    """get_experiment + A6 uuid 404→FS + A3 FS overlay."""
    from map_client.exceptions import MAPNotFoundError

    from cli.experiment_fs import overlay_fs_authority, projection_id_for_fs_ref

    try:
        exp = client.get_experiment(_rid(client, raw))
    except MAPNotFoundError:
        mapped = projection_id_for_fs_ref(str(raw))
        if mapped is None:
            raise
        exp = client.get_experiment(mapped)
    return overlay_fs_authority(exp)


def _require_id(raw: str | None) -> str:
    if raw is None:
        typer.echo("Error: --id is required", err=True)
        raise typer.Exit(2)
    return raw


def _run_lifecycle(
    experiment_id: str,
    *,
    call,
    target_phase: str | None = None,
    executor_persona: str | None = None,
    review_payload: dict[str, Any] | None = None,
    review_filename: str | None = None,
) -> None:
    """API 门禁通过后回写 index.md（A2）。index 存在时先 preflight 拦手改 phase。"""
    from map_fs import ExperimentIndexError

    from cli.experiment_fs import overlay_fs_authority, preflight_index, writeback_after_transition
    from cli.main import _run  # lazy: avoid cycle

    def action(c: MAPClient):
        rid = _rid(c, experiment_id)
        before = c.get_experiment(rid)
        from_phase = enum_value(before.phase)
        planned = target_phase or from_phase
        try:
            preflight_index(before, from_phase, planned)
        except ExperimentIndexError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc
        result = call(c, rid, before)
        after = result if hasattr(result, "phase") else c.get_experiment(rid)
        actual = enum_value(getattr(after, "phase", planned))
        snapshot = after if hasattr(after, "plan_file_path") else before
        persona = executor_persona
        if persona is None and hasattr(after, "executor_agent_id"):
            me = c.get_me()
            if after.executor_agent_id in (None, after.creator_agent_id, me.id):
                persona = getattr(me, "persona", None) or "host"
        try:
            writeback_after_transition(
                snapshot,
                expected_from_phase=from_phase,
                target_phase=actual,
                executor_persona=persona,
                review_payload=review_payload,
                review_filename=review_filename,
            )
        except ExperimentIndexError as exc:
            typer.echo(f"Error: FS write-back rejected: {exc}", err=True)
            raise typer.Exit(1) from exc
        if hasattr(result, "phase"):
            return overlay_fs_authority(result)
        return result

    _run(action, experiment_id=experiment_id)


@experiment_app.command("create")
def experiment_create(
    title: str = typer.Option(..., "--title"),
    plan_file: Path | None = typer.Option(
        None,
        "--plan-file",
        help="Plan MD file to read and send as content (existing behavior).",
    ),
    plan_file_path: str | None = typer.Option(
        None,
        "--plan-file-path",
        help="MAP slimming: store local plan MD file path instead of sending content. "
        "Use as alternative to --plan-file.",
    ),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    description: str | None = typer.Option(None, "--description"),
    submit_for_review: bool = typer.Option(False, "--submit-for-review"),
    topic_id: str | None = typer.Option(
        None,
        "--topic-id",
        help="Topic UUID (DB) or FS topic slug (resolved to its deterministic uuid5, T2-P1).",
    ),
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
    from cli.main import _resolve_project, _run  # lazy: avoid cycle

    if plan_file is None and plan_file_path is None:
        typer.echo("Error: either --plan-file or --plan-file-path is required", err=True)
        raise typer.Exit(2)
    if plan_file is not None and plan_file_path is not None:
        typer.echo("Error: use only one of --plan-file or --plan-file-path", err=True)
        raise typer.Exit(2)

    if plan_file is not None:
        content = _read_text_file(plan_file, kind="plan")
        plan_input = PlanInput(content_md=content)
    else:
        plan_input = PlanInput(file_path=plan_file_path)
    # Local plan frontmatter lint pre-check (a764abf6 I1.(c)). The server
    # has its own hard validator (``assert_plan_frontmatter_ok``), but a
    # local gate saves a round trip and gives a clearer error message
    # when the author simply forgot the YAML block.
    # v0.12 M55D (E8): lint BOTH forms — slim (--plan-file-path) too.
    # Since M55D the server skips content_md validation for the slim
    # form (it has no local file), so this local pre-check is the front
    # gate for slim plans; without it a frontmatter-less plan would
    # reach the DB unchecked (review r1's "no-gate hole").
    if not force_lint_bypass:
        from server.services.plan_marker_service import validate_plan_frontmatter

        lint_content = (
            content
            if plan_file is not None
            else _read_text_file(Path(plan_file_path), kind="plan")
        )
        result = validate_plan_frontmatter(lint_content)
        missing_fields = [
            w.field for w in result.warnings if w.code == "PLAN_MARKER_MISSING_FIELD"
        ]
        if missing_fields or any(
            w.code == "PLAN_MARKER_FRONT_MATTER_MISSING"
            for w in result.warnings
        ):
            keys = ", ".join(sorted(set(missing_fields))) if missing_fields else "(no frontmatter)"
            lint_target = (
                plan_file if plan_file is not None else plan_file_path
            )
            typer.echo(
                f"Error: plan frontmatter lint failed ({keys}). "
                f"Run `map experiment plan validate --plan-file {lint_target}` "
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
        raise typer.Exit(2) from None

    # T2-P1 (A4): --topic-id 声明放宽为 str——uuid 直传(DB 话题),非 uuid 形态
    # 视为 FS 话题 slug → 确定性 uuid5(本地纯函数,无需先 `topic show` 抄 uuid)。
    if topic_id is not None:
        try:
            topic_id = uuid.UUID(topic_id)
        except ValueError:
            from map_fs import topic_id_for_slug

            topic_id = topic_id_for_slug(topic_id)

    payload = ExperimentCreate(
        title=title,
        description=description,
        plan=plan_input,
        submit_for_review=submit_for_review,
        topic_id=topic_id,
        mode=exp_mode,
        plan_file_path=plan_file_path,
    )

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        created = c.create_experiment(pid, payload)
        from cli.experiment_fs import overlay_fs_authority, topic_ref_for_create, write_index_after_create

        me = c.get_me()
        write_index_after_create(
            created,
            plan_file_path=plan_file_path,
            creator_persona=getattr(me, "persona", None) or "host",
            topic_ref=topic_ref_for_create(topic_id),
        )
        return overlay_fs_authority(created)

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
        from cli.experiment_fs import overlay_fs_authority

        pid = _resolve_project(c, project, project_key)
        phase_filter = ExperimentPhase(phase) if phase else None
        items = c.list_experiments(
            pid,
            phase=phase_filter,
            creator_agent_id=creator_agent_id,
            q=q,
            page=page,
            page_size=page_size,
            include_archived=include_archived,
        )
        return [overlay_fs_authority(item) for item in items]

    _run(action, table_renderer=_render_experiment_table)


@experiment_app.command("submit-review")
def experiment_submit_review(experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
) -> None:
    _run_lifecycle(
        experiment_id,
        target_phase="review",
        call=lambda c, rid, _before: c.submit_for_review(rid),
    )


@experiment_app.command("approve")
def experiment_approve(experiment_id: str = typer.Option(..., "--id", help=_ID_HELP)) -> None:
    _run_lifecycle(
        experiment_id,
        target_phase="approved",
        call=lambda c, rid, _before: c.approve_experiment(rid),
    )


@experiment_app.command("start")
def experiment_start(
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
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
    from cli.main import _resolve_executor_agent_id, _resolve_project  # lazy: avoid cycle

    _run_lifecycle(
        experiment_id,
        target_phase="running",
        call=lambda c, rid, _before: (
            c.start_experiment(
                rid,
                executor_agent_id=(
                    _resolve_executor_agent_id(c, _resolve_project(c, None, None), executor)
                    if executor is not None
                    else None
                ),
            )
        ),
    )


@experiment_app.command("cancel")
def experiment_cancel(experiment_id: str = typer.Option(..., "--id", help=_ID_HELP)) -> None:
    """Cancel an experiment (creator only; running/review phases → cancelled).

    State-machine rejections (already cancelled, done, draft misuse) pass
    through unchanged — the server stays the single source of truth.
    """
    _run_lifecycle(
        experiment_id,
        target_phase="cancelled",
        call=lambda c, rid, _before: c.cancel_experiment(rid),
    )


@experiment_app.command("pre-complete")
def experiment_pre_complete(
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
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

    resolved_id: uuid.UUID | None = None

    def _action(client: MAPClient):
        nonlocal resolved_id
        exp = client.get_experiment(_rid(client, experiment_id))
        resolved_id = exp.id
        # v0.12 M54C (plan.md L31): drop the nested ``ok`` — the outer
        # envelope carries it (docs/CLI-JSON-SCHEMA.md); a data-level
        # ``ok`` collided with the envelope contract.
        return {
            "experiment_id": str(exp.id),
            "phase": exp.phase,
            "current_plan_version": exp.current_plan_version,
            "evidence_keys": sorted(str(key) for key in metadata) if isinstance(metadata, dict) else [],
        }

    _run(_action)
    # T3-S1 (cli-hygiene-batch / A6): 校验通过后回显下一步可直接粘贴的
    # complete 命令行——--id/--metadata 按本次入参填好,--summary 与
    # --log-file-path 是 complete 侧必填、pre-complete 未接收的参数,作为
    # 显式占位交给 host 补全(M55 recovery_command 形态)。
    # 走 stderr（err=True）：json 模式下 stdout 只含一份信封
    # （docs/cli-json-output.md），人类提示行不得污染 stdout
    # （tests/test_cli_json_schema.py 护栏）。
    typer.echo(
        "\nNext (copy-paste, fill in <summary> and <log.md>):\n"
        f"  experiment complete --id {resolved_id} --metadata {metadata_file} "
        "--summary '<summary>' --log-file-path <log.md>",
        err=True,
    )


@experiment_app.command("complete")
def experiment_complete(
    # --id/--summary 声明为可选：--schema 模式打印模板即退出（无需任何参数）。
    # 历史上这里写 required=True 却仍可用，靠的是 typer×click 必填校验失效
    # （见 cli/subcommand_format.py）；校验恢复后改为显式条件校验。
    experiment_id: str | None = typer.Option(None, "--id", help=_ID_HELP),
    summary: str | None = typer.Option(None, "--summary"),
    log_file: Path | None = typer.Option(
        None,
        "--file",
        help="Log MD file to read and send as content (existing behavior). "
        "Use when the full log body should land in MAP (similarity check runs on full text).",
    ),
    log_file_path: str | None = typer.Option(
        None,
        "--log-file-path",
        help="MAP slimming: store local log MD file path instead of sending content. "
        "Use as alternative to --file; use when you only need the path recorded "
        "(slim form, skips similarity).",
    ),
    metadata_file: Path | None = typer.Option(None, "--metadata"),
    allow_missing_evidence: bool = typer.Option(
        False,
        "--allow-missing-evidence",
        help="Bypass metadata evidence check for non-deployment experiments.",
    ),
    known_failures: list[str] = typer.Option(
        None,
        "--known-failures",
        help="50cddb7e I4 (A3): known-failure debt ref(s) waiving the "
        "pytest_summary.failed>0 hard gate. Repeatable.",
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
    if schema:
        _print_complete_metadata_schema_and_exit()
    if experiment_id is None or summary is None:
        typer.echo("Error: --id and --summary are required (unless --schema)", err=True)
        raise typer.Exit(2)
    if log_file is None and log_file_path is None:
        typer.echo("Error: either --file or --log-file-path is required", err=True)
        raise typer.Exit(2)
    if log_file is not None and log_file_path is not None:
        typer.echo("Error: use only one of --file or --log-file-path", err=True)
        raise typer.Exit(2)
    metadata = _load_complete_metadata(
        metadata_file,
        allow_missing_evidence=allow_missing_evidence,
    )
    content_md = _read_text_file(log_file, kind="log") if log_file else None
    payload = ExperimentComplete(
        summary=summary,
        content_md=content_md,
        metadata=metadata,
        log_file_path=log_file_path,
        known_failures=known_failures or [],
    )
    _run_lifecycle(
        experiment_id,
        call=lambda c, rid, _before: c.complete_experiment(rid, payload),
        review_payload={"event": "complete", "summary": summary},
        review_filename="complete.yaml",
    )


@experiment_app.command("accept-result")
def experiment_accept_result(
    # 可选声明 + 显式条件校验，原因同 experiment_complete（--schema 早退）。
    experiment_id: str | None = typer.Option(None, "--id", help=_ID_HELP),
    summary: str | None = typer.Option(None, "--summary"),
    log_file: Path | None = typer.Option(None, "--file"),
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
    if schema:
        _print_review_verdict_schema_and_exit()
    if experiment_id is None or summary is None or log_file is None:
        typer.echo("Error: --id, --summary and --file are required (unless --schema)", err=True)
        raise typer.Exit(2)
    metadata = _read_yaml_file(metadata_file)
    verdict_file = _load_review_verdict_file(review_verdict_file)
    payload = ExperimentResultDecision(
        summary=summary,
        content_md=_read_text_file(log_file, kind="log"),
        metadata=metadata,
        verdict_file=verdict_file,
    )
    _run_lifecycle(
        experiment_id,
        target_phase="done",
        call=lambda c, rid, _before: c.accept_experiment_result(rid, payload),
        review_payload={"decision": "accept", "summary": summary},
        review_filename="accept-result.yaml",
    )


@experiment_app.command("reject-result")
def experiment_reject_result(
    # 可选声明 + 显式条件校验，原因同 experiment_complete（--schema 早退）。
    experiment_id: str | None = typer.Option(None, "--id", help=_ID_HELP),
    summary: str | None = typer.Option(None, "--summary"),
    log_file: Path | None = typer.Option(None, "--file"),
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
    if schema:
        _print_review_verdict_schema_and_exit()
    if experiment_id is None or summary is None or log_file is None:
        typer.echo("Error: --id, --summary and --file are required (unless --schema)", err=True)
        raise typer.Exit(2)
    metadata = _read_yaml_file(metadata_file)
    verdict_file = _load_review_verdict_file(review_verdict_file)
    payload = ExperimentResultDecision(
        summary=summary,
        content_md=_read_text_file(log_file, kind="log"),
        metadata=metadata,
        verdict_file=verdict_file,
    )
    _run_lifecycle(
        experiment_id,
        target_phase="running",
        call=lambda c, rid, _before: c.reject_experiment_result(rid, payload),
        review_payload={"decision": "reject", "summary": summary},
        review_filename="reject-result.yaml",
    )


@experiment_app.command("log")
def experiment_log(
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
    summary: str = typer.Option(..., "--summary"),
    log_file: Path | None = typer.Option(
        None,
        "--file",
        help="Log MD file to read and send as content_md (existing behavior). "
        "Use when the full log body should land in MAP (similarity check runs on full text).",
    ),
    log_file_path: str | None = typer.Option(
        None,
        "--log-file-path",
        help=(
            "MAP slimming (v0.13 M57): local path to the log MD file, sent "
            "as-is without reading; the server stores a stub plus this "
            "path on the log row. Mutually exclusive with --file. Use when "
            "you only need the path recorded (slim form, skips similarity)."
        ),
    ),
    metadata_file: Path | None = typer.Option(None, "--metadata"),
    force_skip_similarity: bool = typer.Option(
        False,
        "--force-skip-similarity",
        help=(
            "b72d0542 I1.b(2)(e): acknowledge the soft content-similarity "
            "warning when the new log body is >= 70%% similar to a prior "
            "log. The warning is suppressed in stdout and a "
            "``log.force_skip`` audit row is written instead. No-op with "
            "--log-file-path (the slim form skips the check entirely)."
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

    v0.13 M57 slim form: ``--log-file-path`` sends only the local path
    (no file round trip). The server stores a stub in content_md, skips
    the content-similarity check (stdout carries ``similarity_skipped:
    slim form``), and fires a non-blocking ``summary_repeat_hint`` when
    the summary exactly repeats the prior log's summary. Evidence
    validation is metadata-driven and behaves identically in both forms.
    """
    from cli.main import _read_text_file, _read_yaml_file, _run  # lazy: avoid cycle
    if log_file is not None and log_file_path is not None:
        typer.echo("Error: use only one of --file or --log-file-path", err=True)
        raise typer.Exit(2)
    # T1-P3 (cli-hygiene-batch / A2): --summary 是必填,但没有内容来源时让
    # pydantic 构造直接抛 ValidationError 会泄漏约 30 行堆栈。前置一行式
    # 错误,exit 2——与上方 --file/--log-file-path 互斥校验对齐。
    if log_file is None and log_file_path is None:
        typer.echo("Error: --summary requires --file or --log-file-path", err=True)
        raise typer.Exit(2)
    metadata = _read_yaml_file(metadata_file)
    if log_file is not None:
        payload = ExperimentLogCreate(
            summary=summary,
            content_md=_read_text_file(log_file, kind="log"),
            metadata=metadata,
            force_skip_similarity=force_skip_similarity,
        )
    else:
        # Slim form: no similarity warning can fire (check skipped), so
        # --force-skip-similarity is not sent — no-op by protocol (M57D).
        payload = ExperimentLogCreate(
            summary=summary,
            file_path=log_file_path,
            metadata=metadata,
        )
    _run(lambda c: c.create_log(_rid(c, experiment_id), payload), experiment_id=experiment_id)


@experiment_app.command("logs")
def experiment_logs(experiment_id: str = typer.Option(..., "--id", help=_ID_HELP)) -> None:
    from cli.main import _run  # lazy: avoid cycle
    _run(lambda c: c.list_logs(_rid(c, experiment_id)), experiment_id=experiment_id)


@experiment_app.command("status")
def experiment_status(
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
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
    from cli.main import _cli_options, _persona_compare_view, _run  # lazy: avoid cycle
    if persona_compare:
        _run(
            lambda c: _persona_compare_view(
                c,
                _rid(c, experiment_id),
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
        result = _load_experiment(client, experiment_id)
        if _cli_options.get("format") == "json":
            # v0.12 M54C (E3/E4, plan.md L31): pure-JSON stdout. The
            # human hints below duplicated data.actions / data.blocked_on /
            # data.phase_owner and broke ``jq`` on the first line. Schema
            # contract: docs/CLI-JSON-SCHEMA.md (payload = ExperimentDetailRead).
            return result
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
    experiment_id: str | None = typer.Option(None, "--id", help=_ID_HELP),
) -> None:
    """Show one experiment (including archived) by UUID, uuid5, slug, or short prefix."""
    from cli.main import _run  # lazy: avoid cycle
    raw = _require_id(experiment_id)
    _run(lambda c: _load_experiment(c, raw), experiment_id=raw)


@experiment_app.command("index-validate")
def experiment_index_validate(
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
    expected_from: str | None = typer.Option(
        None,
        "--expected-from",
        help="Expected current phase in index.md (hand-edit mismatch is rejected).",
    ),
    target: str | None = typer.Option(
        None,
        "--target",
        help="Intended next phase; checked against the allowed-edge table.",
    ),
    mode: str = typer.Option("standard", "--mode"),
) -> None:
    """Local index.md contract validate (A2). Does not call the API."""
    from map_fs import ExperimentIndexError, parse_experiment_dir, validate_experiment_index_file

    from cli.experiment_fs import content_root_name, find_fs_experiment, workspace_root

    workspace = workspace_root()
    if workspace is None:
        typer.echo("Error: .map/config.yaml not found. Run `map bootstrap` first.", err=True)
        raise typer.Exit(1)
    root = content_root_name(workspace)
    slug: str | None = None
    try:
        ref_uuid = uuid.UUID(experiment_id)
    except ValueError:
        slug = experiment_id
    else:
        fs = find_fs_experiment(workspace, ref=ref_uuid)
        slug = fs.slug if fs is not None else None
        if slug is None:
            parsed = parse_experiment_dir(
                workspace / root / "experiments" / experiment_id, workspace
            )
            slug = parsed.slug if parsed is not None else None
    if slug is None:
        typer.echo(f"Error: experiment index not found for {experiment_id}", err=True)
        raise typer.Exit(1)
    try:
        meta = validate_experiment_index_file(
            workspace,
            slug,
            content_root=root,
            expected_from_phase=expected_from,
            target_phase=target,
            mode=mode,
        )
    except ExperimentIndexError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"index.md ok slug={slug} phase={meta.get('phase')} "
        f"plan_version={meta.get('current_plan_version')}"
    )


@experiment_app.command(
    "archive",
    epilog="Use --undo or --unarchive to restore an archived experiment.",
)
def experiment_archive(
    experiment_id: str | None = typer.Option(None, "--id", help=_ID_HELP),
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
    from cli.main import _run  # lazy: avoid cycle
    raw = _require_id(experiment_id)
    from map_types.schemas import ExperimentUpdate

    payload = ExperimentUpdate(archived=not (undo or unarchive))
    object_kind = "experiment"

    def action(c: MAPClient):
        rid = _rid(c, raw)
        try:
            return c.update_experiment(rid, payload)
        except MAPNotFoundError as exc:
            typer.echo(
                f"Error: {object_kind} {rid} not found",
                err=True,
            )
            raise typer.Exit(1) from exc

    _run(action)


@lock_app.command("acquire")
def experiment_lock_acquire(
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
    ttl: int = typer.Option(1800, "--ttl", min=1, help="Lock TTL in seconds."),
) -> None:
    from cli.main import _run  # lazy: avoid cycle
    _run(lambda c: c.acquire_experiment_lock(_rid(c, experiment_id), ttl_seconds=ttl), experiment_id=experiment_id)


@lock_app.command("release")
def experiment_lock_release(experiment_id: str = typer.Option(..., "--id", help=_ID_HELP)) -> None:
    from cli.main import _run  # lazy: avoid cycle
    _run(lambda c: c.release_experiment_lock(_rid(c, experiment_id)), experiment_id=experiment_id)


@lock_app.command("force-release")
def experiment_lock_force_release(
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
    reason: str = typer.Option(..., "--reason"),
    actor: str | None = typer.Option(None, "--actor"),
) -> None:
    from cli.main import _run  # lazy: avoid cycle
    _run(lambda c: c.force_release_experiment_lock(_rid(c, experiment_id), reason=reason, actor=actor), experiment_id=experiment_id)


@lock_app.command("skip")
def experiment_lock_skip(
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
    next_attempt_at: str = typer.Option(..., "--next-attempt-at"),
) -> None:
    from cli.main import _run  # lazy: avoid cycle
    _run(lambda c: c.record_experiment_lock_skip(_rid(c, experiment_id), next_attempt_at=next_attempt_at), experiment_id=experiment_id)


@lock_app.command("scan-stalled")
def experiment_lock_scan_stalled() -> None:
    """Scan running experiment locks and emit no-progress notifications."""
    from cli.main import _run  # lazy: avoid cycle

    _run(lambda c: c.scan_stalled_experiment_locks())


@review_app.command("add")
def review_add(
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
    review_file: Path = typer.Option(..., "--review"),
) -> None:
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
    payload = PlanRevise(
        content_md=_read_text_file(plan_file, kind="plan"),
        change_note=note,
        addressed_item_ids=list(addressed_item),
        breaking_audit=breaking_audit,
    )
    _run_lifecycle(
        experiment_id,
        call=lambda c, rid, _before: c.revise_plan(rid, payload),
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
    from cli.main import _run  # lazy: avoid cycle
    def action(c: MAPClient):
        return c.list_reviews(
            _rid(c, experiment_id),
            include_archived=include_archived,
            plan_version=plan_version,
        )

    _run(action, experiment_id=experiment_id)


@review_app.command("withdraw")
def review_withdraw(
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
    review_id: uuid.UUID = typer.Option(..., "--review-id"),
) -> None:
    from cli.main import _run  # lazy: avoid cycle
    _run(lambda c: c.withdraw_review(_rid(c, experiment_id), review_id), experiment_id=experiment_id)


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
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
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
    _run(lambda c: c.create_comment(_rid(c, experiment_id), payload), experiment_id=experiment_id)


# --- file/metadata helpers + schema templates ------------------------------
# 自 cli/main.py 搬入(size-cap 守卫 1600 行):本模块是唯一消费者,
# 顺带消除原先 ``cli.main ↔ cli.commands.experiment`` 的 lazy-import cycle。
def _read_text_file(path: Path, *, kind: str) -> str:
    """Read a required ``--file``/``--metadata`` argument.

    Converts missing-file and not-a-file OS errors into a clean CLI error
    (exit code 2) instead of letting Python emit a raw traceback, so that
    user-facing mistakes like ``--metadata ./missing.yaml`` stay legible.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        typer.echo(f"Error: {kind} file not found: {path}", err=True)
        raise typer.Exit(2) from None
    except IsADirectoryError:
        typer.echo(f"Error: {kind} path is a directory, not a file: {path}", err=True)
        raise typer.Exit(2) from None


def _read_yaml_file(path: Path | None, *, kind: str = "metadata") -> Any:
    if path is None:
        return None
    return yaml.safe_load(_read_text_file(path, kind=kind))


def _load_complete_metadata(path: Path | None, *, allow_missing_evidence: bool) -> dict | None:
    metadata = _read_yaml_file(path)
    if allow_missing_evidence:
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["allow_missing_evidence"] = True
        return metadata
    if not metadata_has_completion_evidence(metadata):
        keys = ", ".join(sorted(EVIDENCE_METADATA_KEYS))
        # T3-S1 (cli-hygiene-batch / A6): 错误前置给出 accepted keys + 示例
        # JSON 片段——避免「complete 还要再传一遍 metadata」的要求只有在
        # 失败后才知道,试探式重试。完整模板见 `--schema`。
        typer.echo(
            "Error: experiment complete now requires --metadata with deployment/test evidence "
            f"(accepted keys include: {keys}). Use --allow-missing-evidence only for explicit exceptions.\n"
            "  example: {\n"
            '    "api_health": "ok",\n'
            '    "pytest_summary": {"total": 35, "passed": 35, "failed": 0}\n'
            "  }\n"
            "  schema: docs/cli-schemas.md#experiment-complete-metadata\n"
            "  full template: `map experiment complete --schema`",
            err=True,
        )
        raise typer.Exit(2)
    return metadata


def _load_review_verdict_file(path: Path | None):
    """Load and validate a --review-verdict-file YAML against ReviewVerdictFile.

    Returns ``None`` when path is omitted (legacy free-text path). Surface a
    clean CLI error (exit 2) on malformed YAML or schema validation failure —
    never let raw Pydantic tracebacks leak to the reviewer.
    """
    if path is None:
        return None
    from map_types.schemas import ReviewVerdictFile

    raw = _read_yaml_file(path, kind="review-verdict")
    try:
        return ReviewVerdictFile.model_validate(raw)
    except Exception as exc:
        typer.echo(
            f"Error: invalid --review-verdict-file {path}: {exc}\n"
            "  schema: sdk/python/map_types/schemas.py:ReviewVerdictFile "
            "(also see docs/cli-schemas.md#review-verdict-file)",
            err=True,
        )
        raise typer.Exit(2) from None


# --- schema discovery (cli-ux PR1) ------------------------------------------
# `map experiment accept-result --schema` / `map experiment complete --schema`
# print a copy-paste-ready YAML template with field-level hints. This is the
# cheapest fix for "I don't know what fields the SDK wants" — the same
# information as grepping the SDK, but in 200ms via the CLI.
_REVIEW_VERDICT_SCHEMA_YAML = """\
# Review verdict file — schema: sdk/python/map_types/schemas/experiment.py:ReviewVerdictFile
# (also docs/cli-schemas.md#review-verdict-file)
#
# review_id: REQUIRED — UUID from `map experiment review list --id <exp-id>`
# verdicts:  list of per-item verdicts; one verdict per review item
# invariants: optional list of verification checks (item_id, verified, note)
review_id: 00000000-0000-0000-0000-000000000000  # <-- replace
verdicts:
  - item_id: 00000000-0000-0000-0000-000000000000  # <-- replace with review item UUID
    verdict: passed  # accepted values: passed | failed | waived
    # CLI also accepts aliases: accept|reject|dismiss (mapped to canonical values)
    # reason: REQUIRED only when verdict == waived (50-1000 chars)
invariants:
  - item_id: 00000000-0000-0000-0000-000000000000  # <-- replace
    verified: true
    # note: optional, max 1000 chars
"""

_COMPLETE_METADATA_SCHEMA_YAML = """\
# Experiment complete --metadata file —
#   schema: docs/cli-schemas.md#experiment-complete-metadata
#   helper: `map experiment complete --schema` reprints this template
#
# At least ONE of the following keys MUST be present (else --allow-missing-evidence).
# Accepted keys: api_health | alembic_current | pytest_summary | test_summary |
#                smoke | smoke_result | image_digest | health | acceptance
api_health: ok
alembic_current:
  head: "<revision>"  # alembic current revision id
  upgrade_clean: true
pytest_summary:
  total: 0
  passed: 0
  failed: 0
  skipped: 0
smoke:
  api_health: ok
  notes: "..."
"""


def _print_review_verdict_schema_and_exit() -> None:
    """cli-ux PR1: print the review verdict file template + exit 0.

    Lets a reviewer / host learn the schema without grepping the SDK.
    """
    typer.echo(_REVIEW_VERDICT_SCHEMA_YAML.rstrip())
    raise typer.Exit(0)


def _print_complete_metadata_schema_and_exit() -> None:
    """cli-ux PR1: print the experiment-complete metadata template + exit 0."""
    typer.echo(_COMPLETE_METADATA_SCHEMA_YAML.rstrip())
    raise typer.Exit(0)


