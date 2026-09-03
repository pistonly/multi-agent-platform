"""``map experiment ...`` sub-app — cli/main.py split.

Owns experiment create/list/index-validate/sync/lock/comment plus the
shared lifecycle helpers. T33: ``review`` / ``plan`` sub-apps moved to
``cli.commands.experiment_review``; T45: lifecycle commands
(``submit-review``…``archive``) moved to ``cli.commands.experiment_lifecycle``
and query commands (``log/logs/status/show``) to
``cli.commands.experiment_inspect`` — both registered at the bottom of this
module via their ``register()``.
T23：执行链辅助（``_run`` / ``_read_text_file`` 等）已从 ``cli.runner`` /
``cli.io_helpers`` 顶层导入；仅运行时状态（``_cli_options`` 等
monkeypatch 面）保留函数内延迟导入。
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import typer
from map_client.client import MAPClient
from map_client.exceptions import MAPNotFoundError
from map_types.enums import ExperimentMode
from map_types.schemas import (
    ExperimentComplete,
    ExperimentCreate,
    ExperimentResultDecision,
    ExperimentStart,
    PlanInput,
)

from cli import runner  # module ref: test monkeypatch surface (T23)
from cli.io_helpers import _read_text_file
from cli.shortid import resolve_ref
from cli.table_render import enum_value, format_datetime, render_table, short_uuid, truncate

experiment_app = typer.Typer(help="Experiment commands", rich_markup_mode=None)
lock_app = typer.Typer(help="Experiment execution lock commands (per-project).")
experiment_app.add_typer(lock_app, name="lock")
# T33: ``review`` / ``plan`` sub-apps moved to cli.commands.experiment_review;
# they are imported + registered at the bottom of this module (after the
# shared lifecycle helpers that module pulls in).

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
    from cli.shortid import normalize_uuid_like

    text = str(raw).strip()
    if normalize_uuid_like(text) is None and not looks_like_hex_prefix(text):
        mapped = projection_id_for_fs_ref(text)
        if mapped is not None:
            return mapped

    def matcher(prefix: str) -> list[tuple[uuid.UUID, str]]:
        project_id = runner._resolve_project(client, None, None)
        items, _total = client.list_experiments_page(
            project_id, id_prefix=prefix, page_size=50, include_archived=True
        )
        return [(e.id, e.title or "") for e in items]

    return resolve_ref(raw, kind="experiment", matcher=matcher)


def _load_experiment(client: MAPClient, raw: str | uuid.UUID):
    """DB GET + FS overlay；404 时从 index.md 合成（FS-only 目录可 show）。"""
    from map_client.exceptions import MAPNotFoundError

    from cli.experiment_fs import (
        fs_experiment_to_detail,
        lookup_fs_for_ref,
        overlay_fs_authority,
        project_id_from_workspace,
        projection_id_for_fs_ref,
        workspace_root,
    )

    db_error: MAPNotFoundError | None = None
    db_exp = None
    try:
        db_exp = client.get_experiment(_rid(client, raw))
    except MAPNotFoundError as exc:
        db_error = exc
        mapped = projection_id_for_fs_ref(str(raw))
        if mapped is not None:
            try:
                db_exp = client.get_experiment(mapped)
                db_error = None
            except MAPNotFoundError as mapped_exc:
                db_error = mapped_exc

    if db_exp is not None:
        return overlay_fs_authority(db_exp)

    workspace = workspace_root()
    fs = lookup_fs_for_ref(str(raw), workspace) if workspace is not None else None
    if fs is not None and workspace is not None:
        return fs_experiment_to_detail(
            fs, project_id_from_workspace(workspace), workspace
        )
    if db_error is not None:
        raise db_error
    raise MAPNotFoundError(404, f"experiment not found: {raw}")


def _list_api_experiments_all(client: MAPClient, pid: uuid.UUID, **kwargs: Any) -> list[Any]:
    page = 1
    acc: list[Any] = []
    while True:
        batch, total = client.list_experiments_page(
            pid, page=page, page_size=100, **kwargs
        )
        acc.extend(batch)
        if not batch or len(acc) >= total:
            break
        page += 1
        if page > 100:
            break
    return acc


def _slice_page(items: list[Any], page: int, page_size: int) -> list[Any]:
    start = (page - 1) * page_size
    return items[start : start + page_size]


def _require_id(raw: str | None) -> str:
    if raw is None:
        typer.echo("Error: --id is required", err=True)
        raise typer.Exit(2)
    return raw


def resolve_writer_persona(me: Any) -> str:
    """Resolve the persona label for the writer of an FS ``index.md``.

    CLI runtime callers (e.g. ``MAPClient.get_me()``) typically hand back an
    ``AgentRead`` schema that has no ``persona`` field at all — only
    ``id``/``name``/``role``/``project_id``/``project_key``/``created_at``.
    The earlier code ``getattr(me, "persona", None) or "host"`` therefore
    silently attributed every delegated executor write-back to ``host``,
    breaking downstream topic routing for participant / reviewer writers.

    Resolution chain (most specific first):
      1. ``me.persona`` when the schema carries it (future-proofing; not
         yet on ``AgentRead`` but cheap to keep).
      2. ``persona_from_agent_name(me.name)`` — the canonical trailing
         ``-{persona}`` suffix. ``multi-agent-platform-plan-dogfood-participant``
         → ``participant``, ``...-host`` → ``host``,
         ``...-reviewer`` → ``reviewer``.
      3. Conservative ``"host"`` fallback — only if both above miss
         (anonymous / custom agent name without canonical suffix).

    The fallback is deliberately biased toward ``host`` (rather than
    raising) because lifecycle writebacks happen for every phase transition
    and a crash would break every experiment. The fix's correctness
    guarantee is the strict order — never default ``"host"`` while
    ``me.name`` resolves to participant/reviewer.
    """
    explicit = getattr(me, "persona", None)
    if explicit:
        return explicit
    from map_types.persona import persona_from_agent_name

    by_name = persona_from_agent_name(getattr(me, "name", None))
    if by_name:
        return by_name
    return "host"


def _run_lifecycle(
    experiment_id: str,
    *,
    call,
    target_phase: str | None = None,
    executor_persona: str | None = None,
    review_payload: dict[str, Any] | None = None,
    review_filename: str | None = None,
    action: str | None = None,
    start: ExperimentStart | None = None,
    complete: ExperimentComplete | None = None,
    decision: ExperimentResultDecision | None = None,
    start_fn=None,
) -> None:
    """API 门禁通过后回写 index.md（A2）。index 存在时先 preflight 拦手改 phase。

    实验 24f3e565（B3/B7/B8）：``action`` 给出时走两跳协议——validate →
    本地 intent（``.map/intents/``）→ commit → 删 intent；commit 失败时
    intent 留存并提示 ``map experiment recover``。``call``（单跳 SDK 方法）
    仅作为 ``action=None`` 时的遗留路径保留。
    """
    from map_fs import ExperimentIndexError

    from cli.experiment_fs import overlay_fs_authority, preflight_index, writeback_after_transition

    def _action(c: MAPClient):
        from cli.experiment_fs import lifecycle_missing_projection_message

        rid = _rid(c, experiment_id)
        try:
            before = c.get_experiment(rid)
        except MAPNotFoundError as exc:
            hint = lifecycle_missing_projection_message(experiment_id)
            if hint:
                typer.echo(f"Error: {hint}", err=True)
                raise typer.Exit(1) from exc
            raise
        from_phase = enum_value(before.phase)
        planned = target_phase or from_phase
        try:
            preflight_index(before, from_phase, planned)
        except ExperimentIndexError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc
        if action is not None:
            from cli.experiment_transition import two_hop_transition

            start_payload = start
            if start_fn is not None:
                resolved_executor = start_fn(c)
                if resolved_executor is not None:
                    start_payload = ExperimentStart(executor_agent_id=resolved_executor)
                else:
                    start_payload = None
            response = two_hop_transition(
                c,
                rid,
                before,
                action=action,
                start=start_payload,
                complete=complete,
                decision=decision,
            )
            # commit 响应只带回快照摘要；写回与 persona 解析统一用 fresh
            # detail（与遗留单跳路径的语义一致）。complete 的 template
            # soft-validation 是响应侧装饰（b72d0542 I1.b）：commit 响应
            # 带回时注入 fresh detail，让 runner._run 的既有渲染
            # （stderr [WARN] + stdout template_validation 块）原样生效。
            result = c.get_experiment(rid)
            if getattr(response, "template_validation", None) is not None:
                result = result.model_copy(
                    update={"template_validation": response.template_validation}
                )
            after = result
            actual = enum_value(response.phase)
        else:
            result = call(c, rid, before)
            after = result if hasattr(result, "phase") else c.get_experiment(rid)
            actual = enum_value(getattr(after, "phase", planned))
        snapshot = after if hasattr(after, "plan_file_path") else before
        # plan-mode-direct-execution-productization: always derive the
        # executor persona label from the resolved agent, even when the
        # caller passed a full ``agent_name`` to ``--executor``. Without
        # this the FS ``index.md`` records ``executor: host`` (the caller)
        # for delegated runs and the readback cannot tell who is executing.
        persona = executor_persona
        if persona is None and hasattr(after, "executor_agent_id"):
            executor_id = after.executor_agent_id
            me = c.get_me()
            if executor_id is None or executor_id == after.creator_agent_id or executor_id == me.id:
                # Self-exec / creator-exec / no-executor cases: writer persona
                # comes from ``me``. ``AgentRead`` has no ``persona`` field,
                # so resolve_writer_persona falls back to the agent name
                # suffix before defaulting to ``host``.
                persona = resolve_writer_persona(me)
            else:
                from map_types.persona import persona_from_agent_name

                project_id = getattr(after, "project_id", None) or getattr(
                    before, "project_id", None
                )
                if project_id is not None:
                    try:
                        agents = c.list_agents(project_id=project_id)
                    except Exception:  # pragma: no cover - list_agents is runtime
                        agents = []
                    for agent in agents:
                        if getattr(agent, "id", None) == executor_id:
                            label = persona_from_agent_name(getattr(agent, "name", None))
                            if label is not None:
                                persona = label
                            break
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

    runner._run(_action, experiment_id=experiment_id)


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
            # 内联详情：省一次 validate 调用（4 个必填字段缺任一即在此看全）
            for w in result.warnings:
                if w.code in ("PLAN_MARKER_MISSING_FIELD", "PLAN_MARKER_EMPTY_LIST"):
                    typer.echo(f"  - {w.code}: field '{w.field}'", err=True)
                else:
                    typer.echo(f"  - {w.code}: {w.detail or ''}".rstrip(), err=True)
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
        pid = runner._resolve_project(c, project, project_key)
        created = c.create_experiment(pid, payload)
        from cli.experiment_fs import overlay_fs_authority, topic_ref_for_create, write_index_after_create

        me = c.get_me()
        write_index_after_create(
            created,
            plan_file_path=plan_file_path,
            creator_persona=resolve_writer_persona(me),
            topic_ref=topic_ref_for_create(topic_id),
        )
        return overlay_fs_authority(created)

    runner._run(action)


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
    """List experiments in the current project (local map/ folders merged with API).

    Local ``map/experiments/*/index.md`` is the display authority (phase /
    plan version). FS-only directories without a DB projection are included.
    Defaults to a compact table view. Use ``--format yaml`` or
    ``--format json`` for full structured output (scripts / piping).
    """
    from map_types.enums import ExperimentPhase


    def action(c: MAPClient):
        from cli.experiment_fs import (
            filter_experiment_summaries,
            iter_indexed_experiments,
            merge_experiment_summaries,
            overlay_fs_authority,
            should_scan_local_experiments,
            workspace_root,
        )

        pid = runner._resolve_project(c, project, project_key)
        phase_filter = ExperimentPhase(phase) if phase else None
        if should_scan_local_experiments(project, project_key, pid):
            workspace = workspace_root()
            if workspace is not None:
                api_items = _list_api_experiments_all(
                    c, pid, include_archived=include_archived
                )
                merged = merge_experiment_summaries(
                    iter_indexed_experiments(workspace),
                    api_items,
                    pid,
                    workspace,
                )
                return _slice_page(
                    filter_experiment_summaries(
                        merged,
                        phase=phase_filter,
                        creator_agent_id=creator_agent_id,
                        q=q,
                    ),
                    page,
                    page_size,
                )
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

    runner._run(action, table_renderer=_render_experiment_table)
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


def _render_sync_check(result: dict[str, Any]) -> str:
    lines = [
        f"ok: {result.get('ok')}",
        f"matched: {result.get('matched', 0)}",
        f"diffs: {len(result.get('diffs') or [])}",
        f"repairs: {len(result.get('repairs') or [])}",
        f"fs_only: {len(result.get('fs_only') or [])}",
        f"db_only: {len(result.get('db_only') or [])}",
        f"missing_dir: {len(result.get('missing_dir') or [])}",
        f"authority: {result.get('authority', 'index.md')}",
    ]
    for diff in result.get("diffs") or []:
        lines.append(
            f"  diff {diff.get('slug')} {diff.get('field')}: "
            f"fs={diff.get('fs')} db={diff.get('db')}"
        )
    for repair in result.get("repairs") or []:
        lines.append(
            f"  repair {repair.get('slug')} {repair.get('kind')}: db_id={repair.get('db_id')}"
        )
    for missing in result.get("missing_dir") or []:
        lines.append(
            f"  missing_dir {missing.get('plan_file_path')} id={missing.get('id')}"
        )
    return "\n".join(lines)


@experiment_app.command("sync")
def experiment_sync(
    check: bool = typer.Option(
        False,
        "--check",
        help="Diff DB projections against local index.md. Does not rewrite files.",
    ),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    """Reconcile experiment index.md with DB projections (check-only)."""
    from cli.experiment_fs import experiment_sync_check

    if not check:
        typer.echo(
            "Error: currently only `map experiment sync --check` is supported "
            "(no silent rewrite).",
            err=True,
        )
        raise typer.Exit(2)

    failed = {"value": False}

    def action(c: MAPClient):
        pid = runner._resolve_project(c, project, project_key)
        api_items = _list_api_experiments_all(c, pid, include_archived=True)
        result = experiment_sync_check(api_items)
        failed["value"] = not bool(result.get("ok"))
        return result

    runner._run(action, table_renderer=_render_sync_check)
    if failed["value"]:
        raise typer.Exit(1)
@lock_app.command("acquire")
def experiment_lock_acquire(
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
    ttl: int = typer.Option(1800, "--ttl", min=1, help="Lock TTL in seconds."),
) -> None:
    runner._run(lambda c: c.acquire_experiment_lock(_rid(c, experiment_id), ttl_seconds=ttl), experiment_id=experiment_id)


@lock_app.command("release")
def experiment_lock_release(experiment_id: str = typer.Option(..., "--id", help=_ID_HELP)) -> None:
    runner._run(lambda c: c.release_experiment_lock(_rid(c, experiment_id)), experiment_id=experiment_id)


@lock_app.command("force-release")
def experiment_lock_force_release(
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
    reason: str = typer.Option(..., "--reason"),
    actor: str | None = typer.Option(None, "--actor"),
) -> None:
    runner._run(lambda c: c.force_release_experiment_lock(_rid(c, experiment_id), reason=reason, actor=actor), experiment_id=experiment_id)


@lock_app.command("skip")
def experiment_lock_skip(
    experiment_id: str = typer.Option(..., "--id", help=_ID_HELP),
    next_attempt_at: str = typer.Option(..., "--next-attempt-at"),
) -> None:
    runner._run(lambda c: c.record_experiment_lock_skip(_rid(c, experiment_id), next_attempt_at=next_attempt_at), experiment_id=experiment_id)


@lock_app.command("scan-stalled")
def experiment_lock_scan_stalled() -> None:
    """Scan running experiment locks and emit no-progress notifications."""

    runner._run(lambda c: c.scan_stalled_experiment_locks())


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
    runner._run(lambda c: c.create_comment(_rid(c, experiment_id), payload), experiment_id=experiment_id)


# --- file/metadata helpers + schema templates ------------------------------
# T23（2026-08）：``_read_text_file`` / ``_read_yaml_file`` 定义已上移
# ``cli/io_helpers.py``（原自 main.py 搬入后又 re-export 回去，是循环
# import 成因）。本模块从 io_helpers 导入；main.py 的 re-export 兼容层
# 同步改走 io_helpers。


# ---------------------------------------------------------------------------
# T33: ``review`` / ``plan`` sub-app commands live in
# ``cli.commands.experiment_review``; T45: lifecycle / inspect command blocks
# live in ``cli.commands.experiment_lifecycle`` / ``experiment_inspect``. This
# module keeps the shared lifecycle helpers (``_rid`` / ``_run_lifecycle`` /
# ``_ID_HELP`` / ``_load_experiment`` monkeypatch surface). Imported here at
# the bottom so both import orders (via cli.main or direct) resolve.
from cli.commands.experiment_inspect import register as _register_inspect  # noqa: E402
from cli.commands.experiment_lifecycle import register as _register_lifecycle  # noqa: E402

_register_lifecycle(experiment_app)
_register_inspect(experiment_app)

from cli.commands.experiment_review import plan_app, review_app  # noqa: E402

experiment_app.add_typer(review_app, name="review")
experiment_app.add_typer(plan_app, name="plan")
