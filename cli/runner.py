"""CLI 命令执行器（T23 从 main.py 拆出）。

职责：``_run`` 执行链（client 上下文、统一输出、错误渲染）、JSON
error envelope、结果序列化（yaml/json/table）、项目/agent 引用解析。
``cli/commands/*`` 与 ``cli/audit_target.py`` 等模块**顶层直接导入**
本模块，消除原先散布 30+ 处的函数内 ``from cli.main import ...``
lazy import（main ↔ commands 循环 import 的成因）。

模块级可变状态 ``_cli_options`` / ``_transport`` 仍定义在 ``cli.main``
（Typer callback 与大量测试的 monkeypatch 面都指向它）。本模块通过
运行时 ``from cli import main`` 读取——函数调用时 main 必已完成加载，
setattr 替换后的新对象也能被读到，测试兼容性不变。
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from typing import Any

import httpx
import typer
import yaml
from map_client.client import MAPClient
from map_client.exceptions import MAPHTTPError
from pydantic import BaseModel, ConfigDict

from cli.shortid import normalize_uuid_like


def _cli_options() -> dict[str, Any]:
    """Runtime-resolve ``cli.main._cli_options`` (module-level mutable state).

    Deferred resolution keeps monkeypatch-based tests that setattr a fresh
    dict onto ``cli.main`` working, and avoids a top-level import cycle
    (main imports this module at load time).
    """
    from cli import main as _main

    return _main._cli_options


def _transport() -> httpx.BaseTransport | None:
    """Runtime-resolve ``cli.main._transport`` (same rationale as above)."""
    from cli import main as _main

    return _main._transport


def _current_client_ctx() -> Iterator[MAPClient]:
    """Runtime-resolve ``cli.main._client_ctx`` for ``_run`` / error rendering.

    Indirection point for tests: they monkeypatch ``cli.main._client_ctx``
    (and ``_admin_client_ctx``) to inject fake clients. Resolving through
    the main module at call time keeps that injection surface working
    after the runner extraction (T23).
    """
    from cli import main as _main

    return _main._client_ctx()


def _current_admin_client_ctx() -> Iterator[MAPClient]:
    from cli import main as _main

    return _main._admin_client_ctx()


@contextmanager
def _null_client_ctx() -> Iterator[None]:
    """无客户端上下文：local plane 的零 server 命令（如 local-only list）注入。"""
    yield None


# ---------------------------------------------------------------------------
# client contexts
# ---------------------------------------------------------------------------


@contextmanager
def _client_ctx() -> Iterator[MAPClient]:
    # ``resolve_client`` resolved through ``cli.main`` at call time: tests
    # monkeypatch ``cli.main.resolve_client`` to inject stub clients.
    from cli import main as _main
    from cli.project_context import identity_root

    try:
        client = _main.resolve_client(
            persona=_cli_options().get("persona"),
            # A3 双根：身份/API 配置来源根（--config-root > MAP_CONFIG_ROOT >
            # --project-root）；None 保持 resolve_client 既有 ~/.map 兜底。
            project_root=identity_root(),
            transport=_transport(),
        )
    except ValueError as exc:
        if _cli_options().get("format") == "json":
            _emit_json_error_envelope(
                error_code=None,
                message=str(exc),
                hint=None,
                retryable=False,
                recovery_command=None,
            )
        else:
            typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    try:
        yield client
    finally:
        client.close()


def _resolve_admin_api_url() -> str:
    """Resolve the API URL for admin-only commands.

    Admin commands (audit / feedback triage) authenticate with an admin
    token, not a project persona token, so resolution must not depend
    on ``agents.local.yaml``. Prefer the project ``.map/config.yaml``; fall
    back to ``MAP_API_URL`` so admin commands also work outside a
    bootstrapped project.
    """
    from cli import main as _main  # test injection surface: cli.main.load_project_map_config
    from cli.project_context import identity_root

    project_root = identity_root()
    try:
        return _main.load_project_map_config(project_root=project_root).api_url
    except ValueError:
        pass
    api_url = os.environ.get("MAP_API_URL")
    if not api_url:
        raise ValueError(
            "No MAP API URL found for admin command. Set --project-root to a repo "
            "with .map/config.yaml, or set the MAP_API_URL env var."
        )
    return api_url.rstrip("/")


@contextmanager
def _admin_client_ctx() -> Iterator[MAPClient]:
    """Yield a MAPClient authenticated with the admin token.

    Token source: ``MAP_ADMIN_TOKEN`` env, then ``~/.map/admin.yaml``
    (via ``map_client.bootstrap.admin_client``). Mirrors ``_client_ctx``:
    token/api_url resolution failures become a clean ``typer.Exit(1)``
    rather than a propagated ``ValueError``, so callers (``_run`` with
    ``admin=True`` and ``audit_list``'s inline handler) behave exactly
    like the persona path.
    """
    try:
        api_url = _resolve_admin_api_url()
        # ``admin_client`` resolved through ``cli.main`` (test injection
        # surface, same rationale as ``resolve_client`` above).
        from cli import main as _main

        client = _main.admin_client(api_url, transport=_transport())
    except ValueError as exc:
        if _cli_options().get("format") == "json":
            _emit_json_error_envelope(
                error_code=None,
                message=str(exc),
                hint=None,
                retryable=False,
                recovery_command=None,
            )
        else:
            typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    try:
        yield client
    finally:
        client.close()


# ---------------------------------------------------------------------------
# serialization + output
# ---------------------------------------------------------------------------

# Unified JSON output contract:
# When --format json, success: {"ok": true, "data": {...}} to stdout;
#       error: {"ok": false, "error": {"error_code", "message", "hint", "retryable"}} to stderr.
# When --format yaml/table, behavior is unchanged (yaml.safe_dump / table_renderer).
def _print_json(data: Any) -> None:
    """Dump data as JSON to stdout (``--format json``)."""
    typer.echo(json.dumps(_to_jsonable(data), ensure_ascii=False, indent=2))


def emit_json_success(data: Any) -> None:
    """T42：统一成功侧 JSON envelope ``{"ok": true, "data": ...}``（stdout）。

    ``_run`` 之外的直出命令（skill install/upgrade/list、auth reissue 等）
    统一走这里，勿再手拼 ``json.dumps``（错误侧对应
    ``_emit_json_error_envelope``）。
    """
    typer.echo(
        json.dumps({"ok": True, "data": _to_jsonable(data)}, ensure_ascii=False, indent=2)
    )


def _print_yaml(data: Any) -> None:
    """Dump data as YAML to stdout (default / ``--format yaml``)."""
    typer.echo(yaml.safe_dump(_to_yamlable(data), allow_unicode=True, sort_keys=False))


def _to_jsonable(value: Any) -> Any:
    """Convert a value to a JSON-serializable structure.

    Uses the same conversion logic as :func:`_to_yamlable`: pydantic
    models are dumped via ``model_dump(mode="json")``, enums use
    ``.value``, and lists/tuples/dicts are recursively converted.
    """
    return _to_yamlable(value)


def _to_yamlable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_to_yamlable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_yamlable(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_yamlable(item) for key, item in value.items()}
    return value


def _print_warnings(warnings: list[str] | None) -> None:
    for code in warnings or []:
        if code == "no_topic_id":
            typer.echo(
                "Warning: no_topic_id — project has open topics; "
                "consider --topic-id <uuid> to bind this experiment.",
                err=True,
            )
        elif code == "topic_not_ready_for_experiment":
            typer.echo(
                "Warning: topic_not_ready_for_experiment — linked topic is not marked ready; "
                "continuing because this is a host override.",
                err=True,
            )
        else:
            typer.echo(f"Warning: {code}", err=True)


def _emit_evidence_parse_error(parse_error: str | None) -> None:
    """8ac93d4e I1.d: emit a single stderr [WARN] line when the plan
    frontmatter existed but its YAML failed to parse. The structured
    warnings still go to stdout JSON (``validation.warnings == []``);
    stderr only signals the un-parseable plan so the host notices the
    mistake.
    """
    if not parse_error:
        return
    typer.echo(f"[WARN] plan evidence_keys 解析失败: {parse_error}", err=True)


def _emit_template_warnings(template_validation: Any) -> None:
    """b72d0542 I1.c: emit one stderr ``[WARN]`` line per template warning
    so the host notices missing sections / malformed links without
    parsing the YAML/JSON output. The structured
    ``template_validation`` block still surfaces on stdout (via
    ``_print_json``), keeping a single machine-parseable source of truth.

    Stderr lines have the form::

        [WARN] template: <code> (section=<name>) — <detail>

    Soft validation invariant: warnings never block ``complete``; the
    experiment transitions to ``result_review`` regardless. The host is
    expected to add a follow-up log when warnings indicate missing
    sections.
    """
    warnings = getattr(template_validation, "warnings", None) or []
    for warning in warnings:
        code = getattr(warning, "code", None) or "UNKNOWN"
        section = getattr(warning, "section", None)
        detail = getattr(warning, "detail", None)
        parts = [f"[WARN] template: {code}"]
        if section:
            parts.append(f"(section={section})")
        if detail:
            parts.append(f"— {detail}")
        typer.echo(" ".join(parts), err=True)


def _emit_similarity_warning(similarity_warning: Any) -> None:
    """b72d0542 I1.b(2)(e): emit one stderr ``[WARN]`` line when the
    server's content-similarity check fires. The structured
    ``similarity_warning`` block surfaces on stdout via ``_print_json``
    so scripts can react; stderr is the human-friendly nudge to either
    rewrite the log or pass ``--force-skip-similarity``.

    Stderr line shape::

        [WARN] similarity: HIGH_CONTENT_SIMILARITY score=1.00 >= threshold=0.70 (ref_log=<uuid>)

    Suppressed entirely when ``force_skip_similarity=True`` was passed
    (server echoes ``response.force_skip=True`` and the helper short-
    circuits).
    """
    code = getattr(similarity_warning, "code", None) or "UNKNOWN"
    score = getattr(similarity_warning, "score", None)
    threshold = getattr(similarity_warning, "threshold", None)
    ref_log_id = getattr(similarity_warning, "ref_log_id", None)
    parts = [f"[WARN] similarity: {code}"]
    if score is not None and threshold is not None:
        parts.append(f"score={score:.2f} >= threshold={threshold:.2f}")
    if ref_log_id is not None:
        parts.append(f"(ref_log={ref_log_id})")
    typer.echo(" ".join(parts), err=True)


_DEPRECATED_ALIAS_RENAMES: dict[str, str] = {
    "advance_round_pending_since": "stale_since",
    "partition_visibility": "visibility",
}


def detect_deprecated_aliases(payload: Any) -> list[str]:
    """Walk a YAML/JSON-decodable payload and warn when legacy alias keys are present.

    Returns one human-readable warning per deprecated key found anywhere in
    the structure. The output is deterministic (sorted, deduped) so tests
    can assert exact text.
    """
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in _DEPRECATED_ALIAS_RENAMES:
                    found.add(key)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return [
        f"Warning: '{legacy}' is deprecated; use '{_DEPRECATED_ALIAS_RENAMES[legacy]}' instead."
        for legacy in sorted(found)
    ]


def _emit_deprecation_warnings(payload: Any) -> None:
    for msg in detect_deprecated_aliases(payload):
        typer.echo(msg, err=True)


# ---------------------------------------------------------------------------
# error envelope
# ---------------------------------------------------------------------------

# 8a8822b5 (b): canonical JSON error envelope schema. Pydantic BaseModel
# pins the field names + types so any drift fails ``model_validate`` at
# the CLI boundary rather than silently breaking script consumers.
# The contract is the v1 stable surface (per 8a8822b5 plan v2 (b)):
#     { error_code, message, hint?, docs_url?, retryable?, recovery_command? }
# All fields except ``message`` are optional (``None`` when absent) so
# the envelope stays parseable even when the server / SDK does not
# surface every key.
class CLIErrorEnvelope(BaseModel):
    """CLI JSON error envelope (8a8822b5 (b))."""

    model_config = ConfigDict(extra="forbid")

    error_code: str | None = None
    message: str
    hint: str | None = None
    docs_url: str | None = None
    retryable: bool | None = None
    recovery_command: str | None = None


def _emit_json_error_envelope(
    *,
    error_code: str | None,
    message: str,
    hint: str | None,
    retryable: bool | None,
    recovery_command: str | None,
    docs_url: str | None = None,
) -> None:
    """Emit a JSON error envelope to stderr for ``--format json`` (I1(e) + 8a8822b5 (b)).

    The envelope is the canonical contract for scripts; never changes
    field names without bumping the SDK minor version. ``null`` is used
    for missing optional fields so consumers can use ``obj.get(...)``.
    The envelope is built + validated through the :class:`CLIErrorEnvelope`
    Pydantic model so any future drift in field names fails the model
    rather than silently breaking scripts.

    The 8a8822b5 (b) contract adds ``docs_url`` as an optional field so
    error_code consumers can link out to human-readable documentation.
    The server currently does not emit ``docs_url``; consumers should
    treat ``null`` as "no docs entry yet" and fall back to the existing
    ``hint`` / ``error_code`` keys.
    """

    envelope = CLIErrorEnvelope(
        error_code=error_code,
        message=message,
        hint=hint,
        docs_url=docs_url,
        retryable=retryable,
        recovery_command=recovery_command,
    )
    payload = {"ok": False, "error": envelope.model_dump(mode="json")}
    typer.echo(json.dumps(payload, ensure_ascii=False), err=True)


def _emit_maphttp_error(
    exc: MAPHTTPError,
    *,
    experiment_id: uuid.UUID | None,
    output_format: str,
) -> None:
    """Render a MAPHTTPError to stderr in either YAML or JSON shape (I1(e)).

    JSON shape is the canonical envelope scripts can rely on::

        {
          "error_code": "STATE_MACHINE_REJECT_RESULT_MISUSE",
          "message": "host creator cannot reject own result",
          "hint": "single-item rebutted → resolve-item --status rebutted; ...",
          "retryable": false,
          "recovery_command": "map --persona reviewer experiment reject-result --id <uuid>"
        }

    YAML shape keeps the existing human-friendly line + Hint + Escalation
    layout. ``recovery_command`` mirrors ``hint`` here so the two views
    carry the same actionable information — see
    ``sdk/python/map_client/errors.py:RECOVERY_HINTS``.
    """
    error_code = getattr(exc, "error_code", None)
    hint = getattr(exc, "hint", None)
    retryable = getattr(exc, "retryable", None)
    docs_url = getattr(exc, "docs_url", None)
    recovery_command = hint  # hints are already actionable commands

    if output_format == "json":
        _emit_json_error_envelope(
            error_code=error_code,
            message=exc.detail,
            hint=hint,
            retryable=retryable,
            recovery_command=recovery_command,
        )
        return

    suffix = ""
    if error_code:
        suffix += f" [error_code={error_code}]"
    # v0.12 M55E: render the structured envelope fields as separate human
    # readable lines (Hint / Docs / Recover). Multi-line hints (e.g. the
    # M55B frontmatter template) flow through verbatim. Recover mirrors
    # hint by design, so it is only printed when it carries extra info.
    if hint:
        suffix += f"\nHint: {hint}"
    if docs_url:
        suffix += f"\nDocs: {docs_url}"
    if recovery_command and recovery_command != hint:
        suffix += f"\nRecover: {recovery_command}"
    # I1(c): STATE_MACHINE.* errors get an Escalation: line so the user
    # knows who to ping. Only fetch escalation when the error is a
    # state-machine refusal (other error families don't apply the
    # experiment-scoped resolver). Falls back to silent skip if the
    # endpoint itself errors out — never crash on top of an error.
    if error_code and error_code.startswith(("STATE_MACHINE_", "REVIEW_")):
        try:
            with _current_client_ctx() as client:
                target = client.get_escalation_target(experiment_id=experiment_id)
            if target.escalation_target_id is not None:
                suffix += f"\nEscalation: {target.escalation_label} (tier={target.tier})"
        except Exception:  # noqa: BLE001 - escalate lookup must never crash the error path
            pass
    typer.echo(f"Error {exc.status_code}: {exc.detail}{suffix}", err=True)


# ---------------------------------------------------------------------------
# the runner
# ---------------------------------------------------------------------------


def _run(
    action,
    *,
    detect_deprecated: bool = False,
    experiment_id: uuid.UUID | str | None = None,
    output_format: str | None = None,
    admin: bool = False,
    table_renderer=None,
    client_ctx=None,
) -> None:
    """Run an SDK action with MAP-aware error rendering (I1(c)~(e)).

    Args:
        action: Callable that takes a ``MAPClient`` and returns the
            command result (or None).
        detect_deprecated: When True, scan the result for legacy aliases
            and emit deprecation warnings.
        experiment_id: When provided, the CLI uses the
            ``/agents/me/escalation-target`` endpoint on STATE_MACHINE.*
            errors to surface the chosen escalation contact (Tier 1
            override → Tier 2 caller → Tier 2 same-role → Tier 2 admin).
            Since v0.12 M54B command layers pass the raw ``--id`` string
            (possibly a short prefix); a full UUID string is normalized
            offline here, while a short prefix is left as ``None`` (the
            escalation lookup needs a concrete experiment, and the
            command's own action already resolved the prefix via
            ``cli.shortid``).
        output_format: ``"table"`` / ``"yaml"`` / ``"json"``. When None,
            falls back to the global ``--format`` option. List commands
            (``table_renderer`` is not None) default to ``"table"`` when
            the user hasn't explicitly chosen a format; non-list commands
            fall back to ``"yaml"`` when ``"table"`` is passed.
        table_renderer: Optional callable that takes the action result
            and returns a string for ``"table"`` format output. When
            provided, this command is treated as a list command.
        client_ctx: Optional context manager that yields the client.
            When provided (e.g. :func:`_null_client_ctx` for local-plane
            commands), the admin/persona client resolution is skipped.
    """
    if experiment_id is not None and not isinstance(experiment_id, uuid.UUID):
        experiment_id = normalize_uuid_like(experiment_id)
    if output_format is None:
        output_format = _cli_options().get("format", "yaml")

    # List commands (table_renderer is not None) default to table when the
    # user hasn't explicitly chosen a format via --format / env / config.
    if table_renderer is not None and output_format == "yaml":
        source = _cli_options().get("format_source", "default")
        if source == "default":
            output_format = "table"

    # Non-list commands don't support table; fall back to yaml.
    if table_renderer is None and output_format == "table":
        output_format = "yaml"

    # For error rendering, table behaves like yaml (human-friendly).
    error_format = "yaml" if output_format == "table" else output_format

    try:
        ctx = client_ctx if client_ctx is not None else (_current_admin_client_ctx() if admin else _current_client_ctx())
        with ctx as client:
            result = action(client)
        if result is not None:
            warnings = getattr(result, "warnings", None)
            if warnings:
                _print_warnings(warnings)
            # 8ac93d4e I1.d: surface plan frontmatter parse failures on stderr.
            # Result may be a LogCreateResponse wrapper with ``.validation``;
            # older SDK responses don't have it and are no-ops here.
            validation = getattr(result, "validation", None)
            if validation is not None:
                _emit_evidence_parse_error(getattr(validation, "parse_error", None))
            # b72d0542 I1.c: surface 4-段 template soft-validation warnings
            # on stderr. Set by ``complete_experiment`` only (other endpoints
            # return ``template_validation=None``).
            template_validation = getattr(result, "template_validation", None)
            if template_validation is not None:
                _emit_template_warnings(template_validation)
            # b72d0542 I1.b(2)(e): surface content-similarity warning on
            # stderr unless caller acknowledged via --force-skip-similarity
            # (server echoes ``response.force_skip=True`` and omits the
            # warning).
            similarity_warning = getattr(result, "similarity_warning", None)
            if similarity_warning is not None:
                _emit_similarity_warning(similarity_warning)
            # v0.13 M57 slim form: non-blocking anti-abuse hint — the slim
            # form skips the content check, so an exact summary repeat of
            # the prior log is surfaced instead (hint, never a warning).
            summary_repeat_hint = getattr(result, "summary_repeat_hint", None)
            if summary_repeat_hint:
                typer.echo(f"[HINT] summary repeat: {summary_repeat_hint}", err=True)
            if detect_deprecated:
                _emit_deprecation_warnings(_to_yamlable(result))
            if output_format == "table" and table_renderer is not None:
                typer.echo(table_renderer(result))
            elif output_format == "json":
                emit_json_success(result)
            else:
                _print_yaml(result)
    except MAPHTTPError as exc:
        _emit_maphttp_error(exc, experiment_id=experiment_id, output_format=error_format)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        if error_format == "json":
            _emit_json_error_envelope(
                error_code=None,
                message=str(exc),
                hint=None,
                retryable=None,
                recovery_command=None,
            )
        else:
            typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc



# ---------------------------------------------------------------------------
# 拆分后的子模块 re-export（T46，模块 800 行上限）
# ---------------------------------------------------------------------------
# 解析段整体搬到 cli/runner_resolve.py，宿主原样 re-export：调用方一律走
# ``runner._xxx`` 模块属性（T23 约定），monkeypatch 面不变。
from cli.runner_resolve import (  # noqa: E402, F401
    _PERSONA_SHORT_NAMES,
    _load_topic_resolve_payload,
    _require_map_dir,
    _require_option_uuid,
    _resolve_agent_ref,
    _resolve_creator_agent_id,
    _resolve_executor_agent_id,
    _resolve_project,
)
