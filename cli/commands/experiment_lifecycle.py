"""``map experiment`` 生命周期命令 — T45 拆分自 experiment.py。

Owns ``submit-review / approve / start / cancel / pre-complete / complete /
accept-result / reject-result / archive`` plus the completion-metadata /
review-verdict helpers and ``--schema`` templates. Shared lifecycle helpers
(``_rid`` / ``_run_lifecycle`` / ``_require_id`` / ``_ID_HELP``) stay in
``cli.commands.experiment`` — that module is the test monkeypatch surface —
so command bodies import them at call time (T23). The host registers this
module's commands via :func:`register` at its bottom (no import cycle).
"""
from __future__ import annotations

import uuid
from pathlib import Path

import typer
from map_client.client import MAPClient
from map_client.exceptions import MAPNotFoundError
from map_sdk.evidence import (
    EVIDENCE_METADATA_KEYS,
    metadata_has_completion_evidence,
)
from map_types.schemas import (
    ExperimentComplete,
    ExperimentResultDecision,
)

from cli import runner  # module ref: test monkeypatch surface (T23)
from cli.io_helpers import _read_piped_text, _read_text_file, _read_yaml_file
from cli.runner import _resolve_executor_agent_id


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


def register(app: typer.Typer) -> None:
    """Register the lifecycle commands on the host ``experiment_app``."""
    # Decoration-time value: read once while the (fully loaded) host runs
    # this register() call at its module bottom.
    from cli.commands.experiment import _ID_HELP

    @app.command("submit-review")
    def experiment_submit_review(experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
    ) -> None:
        from cli.commands.experiment import _run_lifecycle

        _run_lifecycle(
            experiment_id,
            target_phase="review",
            action="submit-review",
            call=lambda c, rid, _before: c.submit_for_review(rid),
        )


    @app.command("approve")
    def experiment_approve(experiment_id: str = typer.Option(..., "--id", help=_ID_HELP)) -> None:
        from cli.commands.experiment import _run_lifecycle

        _run_lifecycle(
            experiment_id,
            target_phase="approved",
            action="approve",
            call=lambda c, rid, _before: c.approve_experiment(rid),
        )


    @app.command("start")
    def experiment_start(
        experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
        executor: str | None = typer.Option(
            None,
            "--executor",
            help=(
                "Delegate execution to another agent (name, UUID, or persona "
                "short name host/participant/reviewer). The designated executor "
                "becomes the sole non-admin caller allowed to ``complete``. "
                "Omit to self-execute (host runs the experiment)."
            ),
        ),
    ) -> None:
        """Start experiment execution (approved → running, or draft → running in direct mode).

        Migration 042 adds optional executor delegation: pass ``--executor``
        with an agent name, UUID, or persona short name (``host`` /
        ``participant`` / ``reviewer``) to designate who may call
        ``complete``. Resolution order: UUID pass-through → persona short
        name via ``map_types.persona.pick_agent_by_persona`` against
        ``client.list_agents(project_id)`` (with ``project_key`` from
        ``client.get_me()``) → literal ``agent_name`` exact match. The
        CLI never reads ``.map/agents.yaml`` so resolution stays correct
        in remote and isolated projects.

        The host retains all other lifecycle gates (cancel / withdraw / etc).

        v0.10: in ``direct`` mode, the experiment goes from ``draft`` directly
        to ``running`` (skipping review/approved). Use ``--executor participant``
        to delegate execution to the participant persona; the persona string
        is preserved on ``map/experiments/<slug>/index.md`` as the executor
        label so FS readback shows the right owner.
        """
        from cli.commands.experiment import _run_lifecycle
        from cli.runner import _PERSONA_SHORT_NAMES

        executor_persona: str | None = (
            executor if executor in _PERSONA_SHORT_NAMES else None
        )

        _run_lifecycle(
            experiment_id,
            target_phase="running",
            executor_persona=executor_persona,
            action="start",
            start_fn=lambda c: (
                _resolve_executor_agent_id(c, runner._resolve_project(c, None, None), executor)
                if executor is not None
                else None
            ),
            call=lambda c, rid, _before: (
                c.start_experiment(
                    rid,
                    executor_agent_id=(
                        _resolve_executor_agent_id(c, runner._resolve_project(c, None, None), executor)
                        if executor is not None
                        else None
                    ),
                )
            ),
        )


    @app.command("cancel")
    def experiment_cancel(experiment_id: str = typer.Option(..., "--id", help=_ID_HELP)) -> None:
        """Cancel an experiment (creator only; running/review phases → cancelled).

        State-machine rejections (already cancelled, done, draft misuse) pass
        through unchanged — the server stays the single source of truth.
        """
        from cli.commands.experiment import _run_lifecycle

        _run_lifecycle(
            experiment_id,
            target_phase="cancelled",
            action="cancel",
            call=lambda c, rid, _before: c.cancel_experiment(rid),
        )


    @app.command("recover")
    def experiment_recover(
        experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
        gc: bool = typer.Option(
            False,
            "--gc",
            help=(
                "Explicitly clean up intents that are safe to delete: experiment "
                "terminal, or token expired with server confirming not committed. "
                "Without --gc recover is read-only reporting."
            ),
        ),
    ) -> None:
        """Reconcile local lifecycle intents (``.map/intents/``) with server receipts.

        实验 24f3e565（B3/B4/B6）：lifecycle commit 前崩溃/断网后，依本地
        intent + server receipt 三分支对账——

        - ``[committed]``：server 已按该 token 落地（回执在案），本地以
          server 为准；
        - ``[stale]``：实验已终结或 token 过期且 server 确认未提交——
          intent 不可再提交；
        - ``[conflict]``：server 被其它 transition 推进——输出双方证据
          （本地 intent 七元组 vs server phase + 胜出 receipt）；
        - ``[pending]``：token 仍有效且 server 未提交——可重跑原命令
          （重新 validate 换新 token）。

        默认只读；``--gc`` 只删 committed/stale 两类 intent（B6 定案）。
        """
        from cli.commands.experiment import _rid
        from cli.experiment_transition import recover_intents, render_recover_report

        def _action(client: MAPClient):
            from cli.project_context import current_context

            rid = _rid(client, experiment_id)
            report = recover_intents(
                client,
                current_context().workspace_root,
                rid,
                gc=gc,
            )
            return report

        def _render(report: dict) -> str:
            return render_recover_report(report)

        runner._run(_action, experiment_id=experiment_id, table_renderer=_render)

    @app.command("pre-complete")
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
        from cli.commands.experiment import _rid


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

        runner._run(_action)
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


    @app.command("complete")
    def experiment_complete(
        # --id/--summary 声明为可选：--schema 模式打印模板即退出（无需任何参数）。
        # 历史上这里写 required=True 却仍可用，靠的是 typer×click 必填校验失效
        # （见 cli/subcommand_format.py）；校验恢复后改为显式条件校验。
        experiment_id: str | None = typer.Option(None, "--id", help=_ID_HELP),
        summary: str | None = typer.Option(None, "--summary"),
        log_file: Path | None = typer.Option(
            None,
            "--file",
            help="Log MD file to read and send as content. Omit it to read piped stdin. "
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
        from cli.commands.experiment import _run_lifecycle

        if schema:
            _print_complete_metadata_schema_and_exit()
        if experiment_id is None or summary is None:
            typer.echo("Error: --id and --summary are required (unless --schema)", err=True)
            raise typer.Exit(2)
        piped_content = None
        if log_file is None and log_file_path is None:
            piped_content = _read_piped_text(kind="log")
            if piped_content is None:
                typer.echo(
                    "Error: provide --file, --log-file-path, or pipe log text on stdin",
                    err=True,
                )
                raise typer.Exit(2)
        if log_file is not None and log_file_path is not None:
            typer.echo("Error: use only one of --file or --log-file-path", err=True)
            raise typer.Exit(2)
        metadata = _load_complete_metadata(
            metadata_file,
            allow_missing_evidence=allow_missing_evidence,
        )
        content_md = (
            _read_text_file(log_file, kind="log") if log_file else piped_content
        )
        payload = ExperimentComplete(
            summary=summary,
            content_md=content_md,
            metadata=metadata,
            log_file_path=log_file_path,
            known_failures=known_failures or [],
        )
        _run_lifecycle(
            experiment_id,
            action="complete",
            complete=payload,
            call=lambda c, rid, _before: c.complete_experiment(rid, payload),
            review_payload={"event": "complete", "summary": summary},
            review_filename="complete.yaml",
        )


    @app.command("accept-result")
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
        from cli.commands.experiment import _run_lifecycle

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
            action="accept-result",
            decision=payload,
            call=lambda c, rid, _before: c.accept_experiment_result(rid, payload),
            review_payload={"decision": "accept", "summary": summary},
            review_filename="accept-result.yaml",
        )


    @app.command("reject-result")
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
        from cli.commands.experiment import _run_lifecycle

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
            action="reject-result",
            decision=payload,
            call=lambda c, rid, _before: c.reject_experiment_result(rid, payload),
            review_payload={"decision": "reject", "summary": summary},
            review_filename="reject-result.yaml",
        )

    @app.command(
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
        from cli.commands.experiment import _require_id, _rid

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

        runner._run(action)
