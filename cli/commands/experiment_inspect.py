"""``map experiment`` 查询/展示命令 — T45 拆分自 experiment.py。

Owns ``log / logs / status / show``（含 ``--cost`` 成本视图 helpers）。
Shared helpers（``_rid`` / ``_load_experiment`` / ``_require_id`` /
``_ID_HELP``）留守 ``cli.commands.experiment``（monkeypatch 面），命令体
call-time 导入（T23）。宿主底部经 :func:`register` 挂载，无循环导入。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import typer
from map_client.client import MAPClient
from map_client.exceptions import MAPNotFoundError
from map_types.schemas import ExperimentLogCreate

from cli import runner  # module ref: test monkeypatch surface (T23)
from cli.io_helpers import _read_text_file, _read_yaml_file
from cli.persona_compare import _persona_compare_view
from cli.runner import _print_json


def register(app: typer.Typer) -> None:
    """Register the inspect commands on the host ``experiment_app``."""
    from cli.commands.experiment import _ID_HELP

    @app.command("log")
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
        from cli.commands.experiment import _rid

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
        runner._run(lambda c: c.create_log(_rid(c, experiment_id), payload), experiment_id=experiment_id)


    @app.command("logs")
    def experiment_logs(experiment_id: str = typer.Option(..., "--id", help=_ID_HELP)) -> None:

        from cli.commands.experiment import _rid

        def action(c: MAPClient):
            from cli.experiment_fs import fs_logs_for_ref

            try:
                logs = c.list_logs(_rid(c, experiment_id))
            except MAPNotFoundError:
                logs = None
            if logs:
                return logs
            fs_logs = fs_logs_for_ref(experiment_id)
            if fs_logs:
                return fs_logs
            if logs is not None:
                return logs
            raise MAPNotFoundError(404, f"experiment not found: {experiment_id}")

        runner._run(action, experiment_id=experiment_id)

    @app.command("status")
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
        from cli.commands.experiment import _load_experiment, _rid
        from cli.main import _cli_options  # runtime state (monkeypatch surface)
        if persona_compare:
            runner._run(
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

        runner._run(_action)


    @app.command("show")
    def experiment_show(
        experiment_id: str | None = typer.Option(None, "--id", help=_ID_HELP),
        cost: bool = typer.Option(
            False,
            "--cost",
            help="per-实验 token 成本视图（plan §A4 + §A5；YAML 输出含 match_breakdown）",
        ),
        project_root: Path | None = typer.Option(
            None,
            "--project-root",
            help="项目根目录（含 .map/）。默认 cwd。",
        ),
        as_json: bool = typer.Option(
            False,
            "--json",
            help="JSON 输出（结构化；便于脚本消费）。",
        ),
    ) -> None:
        """Show one experiment (including archived) by UUID, uuid5, slug, or short prefix.

        ``--cost`` 切换到 T5-B 成本视图：persona × session_kind 二维 +
        match_breakdown 4 桶 + sanity_warning；缺价目表显式 pricing_unavailable 标记
        （不允许 0 兜底；plan §A6）。
        """
        from cli.commands.experiment import _load_experiment, _require_id

        raw = _require_id(experiment_id)
        if cost:
            runner._run(
                lambda c: _show_experiment_cost(c, raw, project_root, as_json),
                experiment_id=raw,
            )
            return
        runner._run(lambda c: _load_experiment(c, raw), experiment_id=raw)


def _show_experiment_cost(
    client: MAPClient,
    raw: str | uuid.UUID,
    project_root: Path | None,
    as_json: bool,
) -> None:
    """Render per-experiment token cost view (plan §A4 + §A5)。

    Pipeline：SDK list_experiments (ExperimentWindow) → orchestrator.scan →
    layer2 map → attribution → render.aggregate_by_experiment → YAML 输出。
    """
    from cli.commands.experiment import _rid
    from cli.cost_ledger.orchestrator import (
        cost_breakdown_to_yaml_dict,
        render_experiment_view,
    )

    project_id = runner._resolve_project(client, None, None)
    experiments = _fetch_experiment_windows(client, project_id)
    exp_uuid = _rid(client, raw)
    if project_root is not None:
        root = Path(project_root)
    else:
        from cli.project_context import optional_context

        context = optional_context()
        # cost ledger 扫描 root 下的 .map/ 日志；未 bootstrap 目录保持
        # cwd-relative 降级（与 waker_status 同语义）。
        root = context.workspace_root if context is not None else Path(".")
    breakdown = render_experiment_view(
        root,
        experiment_id=str(exp_uuid),
        experiments=experiments,
    )
    out = cost_breakdown_to_yaml_dict(breakdown, pricing_unavailable=True)
    if as_json:
        _print_json(out)
        return
    # YAML-like text output
    typer.echo(f"experiment_id: {out['experiment_id']}")
    typer.echo("persona_breakdown:")
    for persona, cost_dict in (out["persona_breakdown"] or {}).items():  # type: ignore[union-attr]
        typer.echo(f"  {persona}:")
        for k, v in cost_dict.items():  # type: ignore[union-attr]
            typer.echo(f"    {k}: {v}")
    typer.echo("match_breakdown:")
    for k, v in (out["match_breakdown"] or {}).items():  # type: ignore[union-attr]
        typer.echo(f"  {k}: {v}")
    if out["sanity_warning"]:
        typer.echo(f"sanity_warning: {out['sanity_warning']}")
    typer.echo(f"pricing_unavailable: {out['pricing_unavailable']}")


def _fetch_experiment_windows(client: MAPClient, project_id: uuid.UUID) -> list:
    """Fetch all experiments and build ``ExperimentWindow`` list for attribution.

    **Real-data fix（T5-B I4 follow-up）**：
    - ``started_at`` ← ``exp.created_at``（实验创建时间，DB 必有）
    - ``ended_at`` ← ``exp.archived_at`` 或 phase=done 时 ``updated_at``
      （默认 None 表示仍 in-progress；post_accept_continuation 会一直匹配）
      必须显式设 ended_at 才能让 first_experiment_id 语义正确，避免最早实验
      抢占所有 session（ended_at=None 时所有 session 都与最早实验 overlap）

    注意：ExperimentSummaryRead 仅暴露 ``archived_at``；DB done phase 但
    未 archived 的实验无显式 end，**临时**用 ``updated_at`` 兜底（与 done
    状态的语义偏差 < 1 小时可接受；下个 I-step 引入 Phase=Done 显式时间戳）。
    """
    from cli.cost_ledger.attribution import ExperimentWindow

    items = client.list_experiments(project_id, page_size=100)
    windows = []
    for exp in items:
        started_at = exp.created_at.isoformat() if exp.created_at else None
        if not started_at:
            continue
        phase_value = getattr(exp.phase, "value", exp.phase)
        # 优先 archived_at；否则若 phase=done/cancelled 用 updated_at 兜底；其余 None
        if exp.archived_at is not None:
            ended_at = exp.archived_at.isoformat()
        elif phase_value in ("done", "cancelled") and exp.updated_at:
            ended_at = exp.updated_at.isoformat()
        else:
            ended_at = None
        windows.append(
            ExperimentWindow(
                experiment_id=str(exp.id),
                started_at=started_at,
                ended_at=ended_at,
            )
        )
    return windows
