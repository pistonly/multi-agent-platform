import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
import typer
import yaml
from map_client.bootstrap import admin_client, bootstrap_project_map
from map_client.client import MAPClient
from map_client.exceptions import MAPHTTPError
from map_client.project_config import find_map_dir, load_project_map_config, resolve_client

# arch experiment (0519e2a3) PR1: shared SDK umbrella. Later PRs move
# shared helpers here (see plan). The CLI must stay importable even
# when ``map_sdk`` is unavailable, so we don't gate startup on it.
from map_sdk.evidence import (
    EVIDENCE_METADATA_KEYS,
    metadata_has_completion_evidence,
)
from pydantic import BaseModel, ConfigDict

# arch experiment (0519e2a3) PR3/PR5: agent / notification /
# inbound-event / audit sub-app splits. Each module only exports the
# sub-app instance here; the helpers the commands use
# (``_run``, ``_admin_client_ctx``, ``_read_text_file``, ``_print_json``)
# are pulled in lazily inside each command body to break the
# ``cli.main ↔ cli.commands.*`` cycles.
from cli.commands.action import action_app
from cli.commands.agent import agent_app
from cli.commands.audit import audit_app
from cli.commands.docs import docs_app
from cli.commands.experiment import experiment_app
from cli.commands.feedback import feedback_app
from cli.commands.host import host_app
from cli.commands.notification import inbound_event_app, notification_app
from cli.commands.persona import persona_app
from cli.commands.project import project_app
from cli.commands.runtime import runtime_app
from cli.commands.skill import skill_app
from cli.commands.sync import sync_app
from cli.commands.topic import mention_app, todo_app, topic_app
from cli.e2e_collab import e2e_app
from server.domain.models import AgentRole
from server.domain.schemas import TopicResolve

app = typer.Typer(name="map", help="Multi-Agent Platform CLI", rich_markup_mode=None)
# Sub-apps live in cli/commands/*; imported here only to register via
# add_typer. Command bodies lazy-import helpers from this module to
# break ``cli.main ↔ cli.commands.*`` cycles.
app.add_typer(project_app, name="project")
app.add_typer(experiment_app, name="experiment")
app.add_typer(persona_app, name="persona")
app.add_typer(runtime_app, name="runtime")
app.add_typer(agent_app, name="agent")
app.add_typer(notification_app, name="notification")
app.add_typer(inbound_event_app, name="inbound-event")
app.add_typer(audit_app, name="audit")
app.add_typer(topic_app, name="topic")
app.add_typer(mention_app, name="mention")
app.add_typer(todo_app, name="todo")
app.add_typer(action_app, name="action")
app.add_typer(feedback_app, name="feedback")
app.add_typer(docs_app, name="docs")
app.add_typer(e2e_app, name="e2e")
app.add_typer(sync_app, name="sync")
app.add_typer(skill_app, name="skill")
app.add_typer(host_app, name="host")

_transport: httpx.BaseTransport | None = None
_cli_options: dict[str, Any] = {"persona": None, "project_root": None, "format": "yaml"}


# 8a8822b5 (a): N=2 release cutoff. ``MAP_CLI_RELEASE_VERSION`` controls
# whether the post-N=2 behavior is active. The build / release script is
# expected to set this env var at install time once N=2 ships; for now
# we default to "0.x" so legacy yaml stays default. The hard-cutover
# behavior is:
#   * if release >= N=2, ``yaml`` / ``legacy`` formats are force-overridden
#     to ``json`` regardless of flag / env, with a one-shot stderr warning;
#   * if ``.map/config.yaml`` declares ``cli.default_format: yaml`` under
#     N=2 release, the same warning fires and the value is ignored.
_N2_RELEASE_MAJOR = 1  # bump here when N=2 ships
_N2_DEFAULT_RELEASE = "0.9"


def _parse_release_version(raw: str | None) -> tuple[int, int]:
    """Parse ``MAJOR.MINOR`` release tuple; return ``(0, 9)`` on any failure.

    The parser is intentionally permissive — anything we cannot interpret
    is treated as pre-N=2 so we never accidentally hard-cutover a script.
    """
    if not raw:
        return (0, 9)
    raw = raw.strip()
    if not raw:
        return (0, 9)
    parts = raw.split(".")
    try:
        major = int(parts[0])
    except (ValueError, IndexError):
        return (0, 9)
    try:
        minor = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return (major, 0)
    return (major, minor)


def _is_n2_released() -> bool:
    """True iff ``MAP_CLI_RELEASE_VERSION`` parses to ``>= (_N2_RELEASE_MAJOR, 0)``."""
    raw = os.environ.get("MAP_CLI_RELEASE_VERSION")
    major, minor = _parse_release_version(raw)
    if major > _N2_RELEASE_MAJOR:
        return True
    if major < _N2_RELEASE_MAJOR:
        return False
    return minor >= 0  # any minor in the N=2 major line is in release


def _project_cli_default_format(project_root: Path | None) -> str | None:
    """Read ``cli.default_format`` from ``.map/config.yaml``.

    Returns ``None`` when the project root is not provided, when
    ``.map/config.yaml`` is missing, or when the key is absent. The check
    is intentionally narrow — we only look at the explicit key the release
    checklist flips; everything else (including unknown keys) is ignored.
    """
    if project_root is None:
        return None
    config_path = project_root / ".map" / "config.yaml"
    if not config_path.is_file():
        return None
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    cli_block = data.get("cli")
    if not isinstance(cli_block, dict):
        return None
    value = cli_block.get("default_format")
    if not isinstance(value, str):
        return None
    return value.strip().lower() or None


def _apply_n2_hard_cutover(
    *,
    current_format: str,
    current_source: str,
    project_root: Path | None,
) -> tuple[str, str, list[str]]:
    """Apply 8a8822b5 (a) post-N=2 yaml hard-cutover.

    Returns ``(new_format, new_source, warnings)``. Warnings are emitted
    by the caller; this function is pure so it is unit-testable without
    touching ``typer.echo``.
    """
    if not _is_n2_released():
        return current_format, current_source, []

    warnings: list[str] = []
    config_default = _project_cli_default_format(project_root)
    if config_default == "yaml":
        warnings.append(
            "Warning: .map/config.yaml `cli.default_format: yaml` is "
            "deprecated in N=2; CLI is forcing json output."
        )
        return "json", "n2-release-cutover", warnings

    # Only warn when the user EXPLICITLY asked for yaml (flag / env / config).
    # The implicit default is the CLI's own choice — N=2 silently swaps it
    # to json without a deprecation warning, since the user never asked for
    # yaml in the first place.
    if current_format == "yaml" and current_source in {
        "explicit --format",
        "MAP_CLI_FORMAT env",
        "config cli.default_format",
    }:
        warnings.append(
            "Warning: yaml output format is removed in N=2; "
            "CLI is forcing json output."
        )
        return "json", "n2-release-cutover", warnings

    # Silent default-yaml → json transition (no warning).
    if current_format == "yaml" and current_source == "default":
        return "json", "n2-release-cutover", []

    return current_format, current_source, warnings


@app.callback()
def cli_global_options(
    persona: str | None = typer.Option(
        None,
        "--persona",
        "-p",
        help="Persona from .map/agents.local.yaml (host, participant, reviewer, …)",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="Code repo root containing .map/ (default: search upward from cwd)",
    ),
    output_format: str | None = typer.Option(
        None,
        "--format",
        "-o",
        help=(
            "Output format: 'table' (list commands default) renders a compact "
            "scannable table; 'yaml' (default for non-list commands) dumps the "
            "full structured object; 'json' emits machine-parseable JSON to "
            "stdout with a structured error envelope on stderr. 'legacy' is an "
            "alias for 'yaml' and will be removed in N=2. "
            "Explicit --format always wins over the MAP_CLI_FORMAT env var."
        ),
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Shortcut for --format json. Overrides --format and MAP_CLI_FORMAT.",
    ),
) -> None:
    # 8a8822b5 (f): resolve --format / MAP_CLI_FORMAT priority.
    # --json shortcut > Explicit --format flag > MAP_CLI_FORMAT env var > default 'yaml'.
    env_format = os.environ.get("MAP_CLI_FORMAT", "").strip().lower() or None
    resolved: str | None
    source: str
    if json_output:
        resolved = "json"
        source = "explicit --json"
    elif output_format is not None:
        resolved = output_format.lower()
        source = "explicit --format"
        if env_format and env_format != resolved:
            typer.echo(
                f"Warning: explicit --format={resolved} overrides "
                f"MAP_CLI_FORMAT={env_format}",
                err=True,
            )
    elif env_format:
        resolved = env_format
        source = "MAP_CLI_FORMAT env"
    else:
        resolved = "yaml"
        source = "default"

    # 8a8822b5 (a): post-N=2 — ``.map/config.yaml cli.default_format`` becomes
    # an explicit project-level source (sits between env var and the implicit
    # default). Pre-N=2 keeps the legacy default to avoid breaking existing
    # scripts that have not migrated.
    if (
        _is_n2_released()
        and output_format is None
        and env_format is None
    ):
        config_default = _project_cli_default_format(project_root)
        if config_default in ("yaml", "json"):
            resolved = config_default
            source = "config cli.default_format"

    if resolved == "legacy":
        typer.echo(
            "Warning: MAP_CLI_FORMAT=legacy is deprecated; "
            "use 'yaml' explicitly. The 'legacy' alias will be removed in N=2.",
            err=True,
        )
        resolved = "yaml"

    if resolved not in ("yaml", "json", "table"):
        typer.echo(
            f"Error: unknown --format {resolved!r}; expected 'table', 'yaml', 'json', or 'legacy'.",
            err=True,
        )
        raise typer.Exit(2)

    # 8a8822b5 (a): post-N=2 yaml hard-cutover.
    resolved, source, n2_warnings = _apply_n2_hard_cutover(
        current_format=resolved,
        current_source=source,
        project_root=project_root,
    )
    for warning in n2_warnings:
        typer.echo(warning, err=True)

    _cli_options["persona"] = persona
    _cli_options["project_root"] = project_root
    _cli_options["format"] = resolved
    _cli_options["format_source"] = source


@contextmanager
def _client_ctx() -> Iterator[MAPClient]:
    try:
        client = resolve_client(
            persona=_cli_options.get("persona"),
            project_root=_cli_options.get("project_root"),
            transport=_transport,
        )
    except ValueError as exc:
        if _cli_options.get("format") == "json":
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
    token, not a project persona token, so resolution must not depend on
    ``agents.local.yaml``. Prefer the project ``.map/config.yaml``; fall
    back to ``MAP_API_URL`` so admin commands also work outside a
    bootstrapped project.
    """
    project_root = _cli_options.get("project_root")
    try:
        return load_project_map_config(project_root=project_root).api_url
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
        client = admin_client(api_url, transport=_transport)
    except ValueError as exc:
        if _cli_options.get("format") == "json":
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


# Unified JSON output contract:
# When --format json, success: {"ok": true, "data": {...}} to stdout;
#       error: {"ok": false, "error": {"error_code", "message", "hint", "retryable"}} to stderr.
# When --format yaml/table, behavior is unchanged (yaml.safe_dump / table_renderer).
def _print_json(data: Any) -> None:
    """Dump data as YAML to stdout (used for yaml/legacy format)."""
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


# 0db51e10 I1(5a): per-persona view diff for ``map experiment status
# --persona-compare``. Each persona calls ``get_experiment`` with its own
# token; the four diff_fields are surfaced in a compact table by default
# or as raw YAML via ``--raw``. Defaults to every persona known in
# ``.map/agents.local.yaml`` (sorted); ``--for-personas`` overrides.
_PERSONA_COMPARE_DIFF_FIELDS: tuple[str, ...] = (
    "actions",
    "blocked_on",
    "phase_owner",
    "informational_only",
)


# 0db51e10 I4(5a partition): four-way classification of the
# ``acceptance_status`` partition across personas. plan v2 (a) calls for
# these four buckets to be unit-tested for the ``--persona-compare`` view:
#
#   * ``all_agree``         — every persona sees the same set of
#                             acceptance_status entries.
#   * ``partial_diff``      — at least two personas agree on some
#                             entries but at least one differs.
#   * ``full_diff``         — every persona's set is distinct (no
#                             pairwise equality).
#   * ``cross_phase_fold``  — at least one persona is folded via
#                             ``hidden_for_current_persona=True``;
#                             acceptance_status is collapsed by the
#                             server-side phase whitelist, so the
#                             diff is reported as "folded" instead
#                             of being compared element-wise.
#
# The classifier is a pure function over the already-fetched ``views``
# dict (persona -> payload dict). It does NOT fetch any data itself;
# the caller is responsible for collecting ``hidden_for_current_persona``
# and ``acceptance_status`` from each persona's response.
_PERSONA_COMPARE_PARTITION_ALL_AGREE = "all_agree"
_PERSONA_COMPARE_PARTITION_PARTIAL_DIFF = "partial_diff"
_PERSONA_COMPARE_PARTITION_FULL_DIFF = "full_diff"
_PERSONA_COMPARE_PARTITION_CROSS_PHASE_FOLD = "cross_phase_fold"


def _classify_persona_compare_partition(
    views: dict[str, dict[str, Any]],
) -> str:
    """Classify the ``acceptance_status`` partition across personas.

    The four-way bucket is the unit-test target of plan v2 (a). Order of
    precedence is fixed:

    1. ``cross_phase_fold`` wins if any persona has
       ``hidden_for_current_persona=True`` — the diff is reported as a
       phase-whitelist fold rather than an acceptance_status diff, so
       we never want to misleadingly classify it as ``partial_diff`` or
       ``full_diff``.
    2. ``full_diff`` if no two personas see the same ``acceptance_status``
       set (every pair is disjoint).
    3. ``partial_diff`` if at least two personas agree but not all.
    4. ``all_agree`` as the default — every persona sees the same set.

    ``views`` is keyed by persona name; values are per-persona payload
    dicts (as returned by ``MAPClient.get_experiment`` and serialised
    via ``model_dump(mode="json")``).

    Returns one of the four ``_PERSONA_COMPARE_PARTITION_*`` constants.
    """
    if not views:
        # No data to compare — degenerate case; fold is the safest
        # answer (matches "we cannot tell what the others see").
        return _PERSONA_COMPARE_PARTITION_CROSS_PHASE_FOLD

    hidden = [
        persona
        for persona, payload in views.items()
        if payload.get("hidden_for_current_persona") is True
    ]
    if hidden:
        return _PERSONA_COMPARE_PARTITION_CROSS_PHASE_FOLD

    # Build frozenset of (id, evidence_provided, reviewer_verdict) per
    # persona so two personas with semantically identical acceptance
    # entries hash to the same bucket.
    def _signature(payload: dict[str, Any]) -> frozenset:
        rows = payload.get("acceptance_status") or []
        return frozenset(
            (
                str(entry.get("id")),
                bool(entry.get("evidence_provided", False)),
                entry.get("reviewer_verdict"),
            )
            for entry in rows
        )

    signatures = {persona: _signature(payload) for persona, payload in views.items()}
    distinct = set(signatures.values())

    if len(views) == 1 or len(distinct) == 1:
        return _PERSONA_COMPARE_PARTITION_ALL_AGREE
    if len(distinct) == len(views):
        return _PERSONA_COMPARE_PARTITION_FULL_DIFF
    return _PERSONA_COMPARE_PARTITION_PARTIAL_DIFF


def _persona_compare_view(
    host_client: MAPClient,
    experiment_id: uuid.UUID,
    *,
    personas: list[str] | None = None,
    raw: bool = False,
) -> None:
    """Diff one experiment's per-actor view across multiple personas.

    Each persona's view is fetched via its own bearer token
    (``ProjectMapConfig.client_for``), so the snapshot reflects what that
    persona would actually see when running ``map experiment status`` —
    including the per-actor ``actions`` / ``blocked_on`` computed by
    ``compute_experiment_capabilities``.

    The output is a compact diff table by default; ``--raw`` prints the
    full per-persona snapshot as YAML (useful for snapshot testing and
    e2e diff review).

    0db51e10 I2(5e): after rendering, write an audit row
    (``action=cross_persona_call``) capturing the per-field persona view
    diff + result_partition_count + diff_size. ``host_client`` is the
    default host-token client supplied by ``_run``; audit write failures
    are surfaced as stderr warnings but do NOT block the table output.
    """
    map_dir = find_map_dir(_cli_options.get("project_root"))
    if map_dir is None:
        typer.echo(
            "Error: --persona-compare needs .map/agents.local.yaml; "
            "run `map bootstrap` first.",
            err=True,
        )
        raise typer.Exit(2)
    config = load_project_map_config(map_dir=map_dir)

    selected = personas or sorted(config.tokens.keys())

    views: dict[str, dict[str, Any]] = {}
    for persona in selected:
        if persona not in config.tokens:
            typer.echo(
                f"[skip] {persona}: no token in .map/agents.local.yaml",
                err=True,
            )
            continue
        client = MAPClient(config.api_url, config.tokens[persona], transport=_transport)
        try:
            exp = client.get_experiment(experiment_id)
            if hasattr(exp, "model_dump"):
                payload = exp.model_dump(mode="json")
            elif isinstance(exp, dict):
                payload = exp
            elif hasattr(exp, "__dict__"):
                # SimpleNamespace / dataclass-like object: serialize via __dict__.
                payload = {
                    k: v for k, v in vars(exp).items() if not k.startswith("_")
                }
            else:
                payload = {}
            views[persona] = payload
        except Exception as exc:
            typer.echo(f"[skip] {persona}: {exc}", err=True)
        finally:
            client.close()

    if not views:
        typer.echo("Error: no persona view succeeded; aborting.", err=True)
        raise typer.Exit(2)

    if raw:
        typer.echo(
            yaml.safe_dump(
                _to_yamlable({k: v for k, v in views.items()}),
                allow_unicode=True,
                sort_keys=False,
            )
        )
        return

    persona_names = list(views.keys())
    col_widths = [
        max(len(field), max((len(_format_compare_value(views[p].get(field))) for p in persona_names), default=0))
        for field in _PERSONA_COMPARE_DIFF_FIELDS
    ]

    def _row(values: list[str]) -> str:
        return "  ".join(v.ljust(w) for v, w in zip(values, [max(len("field"), max(col_widths))] + col_widths, strict=False))

    typer.echo(f"persona_compare (experiment {experiment_id}):")
    typer.echo(_row(["field", *persona_names]))
    for field in _PERSONA_COMPARE_DIFF_FIELDS:
        typer.echo(_row([field, *[_format_compare_value(views[p].get(field)) for p in persona_names]]))

    differing = [
        field
        for field in _PERSONA_COMPARE_DIFF_FIELDS
        if len({_format_compare_value(views[p].get(field)) for p in persona_names}) > 1
    ]
    if differing:
        typer.echo(f"diff fields (vary across personas): {', '.join(differing)}")
    else:
        typer.echo("diff fields: (none — all selected personas agree)")

    # 0db51e10 I4(5a partition): four-way acceptance_status partition
    # classification — plan v2 (a) requires the CLI to surface the bucket
    # so reviewers can quickly tell all-agree / partial / full / folded.
    partition = _classify_persona_compare_partition(views)
    typer.echo(f"acceptance_status partition: {partition}")

    # 0db51e10 I2(5e): write audit row via host-token client. Audit write
    # failure does not block the user-facing diff table output.
    _write_cross_persona_call_audit(
        host_client=host_client,
        experiment_id=experiment_id,
        views=views,
        differing_fields=differing,
        acceptance_partition=partition,
    )


def _write_cross_persona_call_audit(
    *,
    host_client: MAPClient,
    experiment_id: uuid.UUID,
    views: dict[str, dict[str, Any]],
    differing_fields: list[str],
    acceptance_partition: str | None = None,
) -> None:
    """0db51e10 I2(5e): best-effort audit write.

    Builds the ``visibility_diff`` payload (per-field persona view dict)
    and the ``diff_size`` / ``result_partition_count`` counters, then
    calls ``MAPClient.record_cross_persona_call``. Any failure is
    reported to stderr but does not raise — the diff table has already
    been rendered by the time we get here, and we never want the audit
    write to mask the user's primary output.

    ``acceptance_partition`` is the four-way bucket label produced by
    ``_classify_persona_compare_partition`` (plan v2 (a)). When provided,
    it is folded into the audit ``visibility_diff`` payload under the
    ``acceptance_status_partition`` key so admin audit queries can
    filter by bucket without re-deriving the classifier.
    """
    visibility_diff: dict[str, dict[str, Any]] = {}
    for field in _PERSONA_COMPARE_DIFF_FIELDS:
        visibility_diff[field] = {
            persona: views[persona].get(field) for persona in views
        }
    if acceptance_partition is not None:
        visibility_diff["acceptance_status_partition"] = acceptance_partition
    try:
        host_client.record_cross_persona_call(
            experiment_id,
            visibility_diff=visibility_diff,
            result_partition_count=len(views),
            diff_size=len(differing_fields),
        )
    except Exception as exc:
        typer.echo(f"[warn] audit write failed: {exc}", err=True)


def _format_compare_value(value: Any) -> str:
    """Render a per-persona diff cell.

    Lists are rendered as ``[a, b]`` so reviewers can scan the action
    set at a glance; ``None`` and empty lists are distinguished (the
    former means the persona cannot see any action hint at all; the
    latter means the action set is empty).
    """
    if value is None:
        return "(none)"
    if isinstance(value, list):
        return "[" + ", ".join(str(item) for item in value) + "]"
    return str(value)


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
    ``similarity_warning`` block surfaces on stdout via
    ``_print_json`` so scripts can react; stderr is the human-friendly
    nudge to either rewrite the log or pass ``--force-skip-similarity``.

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


_CLEAR_ACTION_TEMPLATES: dict[str, str] = {
    "comment": "map topic comment --topic {topic_id} --reply-to {source_comment_id}",
    "ack": "map topic advance-round --topic {topic_id} --ack accept",
    "dismiss": "map mention dismiss --id {mention_id}",
    "read": "Read latest comments on topic '{topic_title}'",
}


def render_clear_action_template(work_item: dict[str, Any]) -> str:
    """Render a deterministic CLI hint for a topic work item's clear_action.

    Returns the literal string for the ``clear_action`` value with the
    placeholder fields replaced from the work item payload. The four
    ``clear_action`` values are mapped to the matching ``map`` CLI
    command; ``read`` is a free-form reading instruction (no CLI verb).

    The output is purely mechanical — no LLM, no environment lookups —
    so callers can diff the rendered strings in tests.
    """
    clear_action = work_item.get("clear_action")
    template = _CLEAR_ACTION_TEMPLATES.get(clear_action or "")
    if template is None:
        return f"(unknown clear_action: {clear_action})"
    if clear_action == "read":
        topic_title = work_item.get("topic_title") or "(untitled)"
        return template.format(topic_title=topic_title)
    if clear_action == "dismiss":
        mention_id = (
            work_item.get("mention_id")
            or work_item.get("idempotency_key")
            or work_item.get("source_comment_id")
            or ""
        )
        return template.format(mention_id=mention_id)
    topic_id = work_item.get("topic_id") or ""
    source_comment_id = work_item.get("source_comment_id") or ""
    return template.format(topic_id=topic_id, source_comment_id=source_comment_id)


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


def _run(
    action,
    *,
    detect_deprecated: bool = False,
    experiment_id: uuid.UUID | None = None,
    output_format: str | None = None,
    admin: bool = False,
    table_renderer=None,
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
        output_format: ``"table"`` / ``"yaml"`` / ``"json"``. When None,
            falls back to the global ``--format`` option. List commands
            (``table_renderer`` is not None) default to ``"table"`` when
            the user hasn't explicitly chosen a format; non-list commands
            fall back to ``"yaml"`` when ``"table"`` is passed.
        table_renderer: Optional callable that takes the action result
            and returns a string for ``"table"`` format output. When
            provided, this command is treated as a list command.
    """
    if output_format is None:
        output_format = _cli_options.get("format", "yaml")

    # List commands (table_renderer is not None) default to table when the
    # user hasn't explicitly chosen a format via --format / env / config.
    if table_renderer is not None and output_format == "yaml":
        source = _cli_options.get("format_source", "default")
        if source == "default":
            output_format = "table"

    # Non-list commands don't support table; fall back to yaml.
    if table_renderer is None and output_format == "table":
        output_format = "yaml"

    # For error rendering, table behaves like yaml (human-friendly).
    error_format = "yaml" if output_format == "table" else output_format

    try:
        ctx = _admin_client_ctx() if admin else _client_ctx()
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
            if detect_deprecated:
                _emit_deprecation_warnings(_to_yamlable(result))
            if output_format == "table" and table_renderer is not None:
                typer.echo(table_renderer(result))
            elif output_format == "json":
                typer.echo(
                    json.dumps(
                        {"ok": True, "data": _to_jsonable(result)},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                _print_json(result)
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
    if hint:
        suffix += f"\nHint: {hint}"
    # I1(c): STATE_MACHINE.* errors get an Escalation: line so the user
    # knows who to ping. Only fetch escalation when the error is a
    # state-machine refusal (other error families don't apply the
    # experiment-scoped resolver). Falls back to silent skip if the
    # endpoint itself errors out — never crash on top of an error.
    if error_code and error_code.startswith(("STATE_MACHINE_", "REVIEW_")):
        try:
            with _client_ctx() as client:
                target = client.get_escalation_target(experiment_id=experiment_id)
            if target.escalation_target_id is not None:
                suffix += f"\nEscalation: {target.escalation_label} (tier={target.tier})"
        except Exception:  # noqa: BLE001 - escalate lookup must never crash the error path
            pass
    typer.echo(f"Error {exc.status_code}: {exc.detail}{suffix}", err=True)


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


def _require_option_uuid(value: uuid.UUID | None, *, option: str = "--id") -> uuid.UUID:
    """Typer 0.16 + nested subcommands do not enforce required UUID options."""
    if value is None:
        typer.echo(f"Error: Missing option '{option}'.", err=True)
        raise typer.Exit(2)
    return value


def _require_map_dir(project_root: Path | None = None) -> Path:
    """Require `.map/config.yaml`; exit with bootstrap hint if missing."""
    root = project_root or _cli_options.get("project_root")
    map_dir = find_map_dir(root)
    if map_dir is None:
        from map_client.project_config import missing_map_config_message

        typer.echo(f"Error: {missing_map_config_message()}", err=True)
        raise typer.Exit(1)
    return map_dir


def _resolve_project(client: MAPClient, project: uuid.UUID | None, project_key: str | None) -> uuid.UUID:
    map_dir = find_map_dir(_cli_options.get("project_root"))
    if map_dir is not None:
        try:
            cfg = load_project_map_config(map_dir=map_dir)
            if project is None and project_key is None:
                return client.get_project_by_key(cfg.project_key).id
        except ValueError:
            pass
    cfg_key = None
    try:
        from map_client.config import load_config

        cfg_key = load_config().get("project_key")
    except Exception:
        pass
    key = project_key or cfg_key
    return client.resolve_project_id(project, project_key=key)


def _resolve_creator_agent_id(
    client: MAPClient,
    project_id: uuid.UUID,
    creator: str | None,
    creator_agent_id: uuid.UUID | None,
) -> uuid.UUID | None:
    """Resolve --creator (name or UUID) and --creator-agent-id into a single creator_agent_id.

    - Neither set → None (no filter).
    - Both set with same value → that value (alias use).
    - Both set with different values → error.
    - --creator is a valid UUID → pass through (skip /agents lookup).
    - --creator is a name → look up via list_agents(project_id); exact match within current
      project, ignoring admin rows. 0 hits → error + list available names;
      >1 hits → error (project-internal name collision).
    """
    if not creator:
        return creator_agent_id
    try:
        creator_uuid = uuid.UUID(creator)
    except ValueError:
        creator_uuid = None
    if creator_uuid is not None:
        if creator_agent_id is not None and creator_agent_id != creator_uuid:
            typer.echo(
                "Error: --creator and --creator-agent-id resolve to different UUIDs.",
                err=True,
            )
            raise typer.Exit(1)
        return creator_uuid
    if creator_agent_id is not None:
        typer.echo(
            "Error: --creator is a name but --creator-agent-id was also passed; "
            "pass one or the other.",
            err=True,
        )
        raise typer.Exit(1)
    agents = client.list_agents(project_id=project_id)
    matches = [
        a
        for a in agents
        if a.role.value != "admin" and a.project_id == project_id and a.name == creator
    ]
    if len(matches) == 0:
        available = sorted(
            a.name for a in agents if a.role.value != "admin" and a.project_id == project_id
        )
        available_hint = (
            f" Available agent_name in this project: {', '.join(available)}."
            if available
            else " No project-bound agents found in this project."
        )
        typer.echo(
            f"Error: agent_name '{creator}' not found in current project.{available_hint}",
            err=True,
        )
        raise typer.Exit(1)
    if len(matches) > 1:
        typer.echo(
            f"Error: agent_name '{creator}' matches {len(matches)} agents in current project; "
            "name is ambiguous. Pass --creator-agent-id <UUID> instead.",
            err=True,
        )
        raise typer.Exit(1)
    return matches[0].id


def _resolve_executor_agent_id(
    client: MAPClient,
    project_id: uuid.UUID,
    executor: str,
) -> uuid.UUID:
    """Resolve ``--executor`` (name or UUID) into an ``executor_agent_id``.

    Migration 042: used by ``map experiment start --executor <name|uuid>``
    to delegate execution to another agent. Mirrors the
    ``_resolve_creator_agent_id`` lookup path:

    - Valid UUID → pass through (skip the /agents lookup).
    - Name → look up via ``list_agents(project_id)``; exact match within
      current project, ignoring admin rows. 0 hits → error + list
      available names; >1 hits → error (project-internal name collision).
    """
    try:
        executor_uuid = uuid.UUID(executor)
    except ValueError:
        executor_uuid = None
    if executor_uuid is not None:
        return executor_uuid
    agents = client.list_agents(project_id=project_id)
    matches = [
        a
        for a in agents
        if a.role.value != "admin" and a.project_id == project_id and a.name == executor
    ]
    if len(matches) == 0:
        available = sorted(
            a.name for a in agents if a.role.value != "admin" and a.project_id == project_id
        )
        available_hint = (
            f" Available agent_name in this project: {', '.join(available)}."
            if available
            else " No project-bound agents found in this project."
        )
        typer.echo(
            f"Error: executor '{executor}' not found in current project.{available_hint}",
            err=True,
        )
        raise typer.Exit(1)
    if len(matches) > 1:
        typer.echo(
            f"Error: executor '{executor}' matches {len(matches)} agents in current project; "
            "name is ambiguous. Pass --executor <UUID> instead.",
            err=True,
        )
        raise typer.Exit(1)
    return matches[0].id


def _load_topic_resolve_payload(path: Path) -> TopicResolve:
    text = _read_text_file(path, kind="resolve")
    if path.suffix.lower() in {".yaml", ".yml"}:
        raw = yaml.safe_load(text) or {}
        if not isinstance(raw, dict):
            raise ValueError("resolve YAML must be a mapping")
        return TopicResolve.model_validate(raw)
    return TopicResolve(decision=text)


@app.command("bootstrap")
def map_bootstrap(
    key: str = typer.Option(..., "--key", help="MAP project_key for this code repo"),
    name: str | None = typer.Option(None, "--name", help="Human-readable MAP project name"),
    path: Path | None = typer.Option(None, "--path", help="workspace_path stored on MAP project"),
    description: str | None = typer.Option(None, "--description"),
    api_url: str | None = typer.Option(None, "--api-url", help="MAP API base URL"),
    project_root: Path | None = typer.Option(None, "--project-root", help="Where to write .map/"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing .map/agents.local.yaml"),
) -> None:
    """Register MAP project + persona agents; write .map/ config (requires admin token)."""
    root = (project_root or Path.cwd()).resolve()
    workspace = path or root
    display_name = name or key
    try:
        result = bootstrap_project_map(
            project_key=key,
            project_name=display_name,
            workspace_path=workspace,
            project_root=root,
            api_url=api_url,
            description=description,
            force=force,
            transport=_transport,
        )
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except MAPHTTPError as exc:
        typer.echo(f"Error {exc.status_code}: {exc.detail}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Wrote {result.config.map_dir}/")
    typer.echo(f"MAP project_key={result.config.project_key} id={result.config.project_id}")
    if result.created_project:
        typer.echo("Created new MAP project.")
    else:
        typer.echo("Reused existing MAP project.")
    if result.skipped_agent_names:
        typer.echo(
            "Skipped existing agents (tokens not recoverable): "
            + ", ".join(result.skipped_agent_names),
            err=True,
        )
        typer.echo("Keep your existing .map/agents.local.yaml or delete agents on MAP before re-bootstrap.")
    typer.echo("Personas: " + ", ".join(result.config.tokens.keys()))
    typer.echo("Try: map --persona host status")


@app.command("me")
def map_me() -> None:
    """Alias for `map persona whoami`."""

    def action(c: MAPClient):
        me = c.get_me()
        payload = me.model_dump(mode="json")
        if _cli_options.get("persona"):
            payload["persona"] = _cli_options["persona"]
        elif find_map_dir(_cli_options.get("project_root")):
            payload["persona"] = load_project_map_config(
                project_root=_cli_options.get("project_root")
            ).default_persona
        return payload

    _run(action)


@app.command("todos")
def map_todos() -> None:
    _run(lambda c: c.get_todos())


@app.command("work")
def map_work(
    notification_limit: int = typer.Option(50, "--notification-limit", min=1, max=200),
    notification_category: str = typer.Option(
        "all",
        "--notification-category",
        help="all (human default), wakeable (waker view), or digest",
    ),
    summary: bool = typer.Option(
        False,
        "--summary",
        help="Return the compact 6-bucket by_kind summary instead of the full work snapshot.",
    ),
    include_all_personas: bool = typer.Option(
        False,
        "--include-all-personas",
        help="Include host-only buckets (explicit_only, informational_only, action_items) when the current persona would otherwise hide them.",
    ),
    topics_limit: int = typer.Option(
        10,
        "--summary-topics-limit",
        min=1,
        max=100,
        help="Max topics per summary bucket (default 10).",
    ),
    experiments_limit: int = typer.Option(
        5,
        "--summary-experiments-limit",
        min=1,
        max=50,
        help="Max experiments per summary bucket (default 5).",
    ),
) -> None:
    """Unified work snapshot: whoami + topic-progress + todos + unread notifications.

    CLI defaults to ``all`` so humans see the same unread count as
    ``notification list --unread-only``. Wakers should pass
    ``--notification-category wakeable`` explicitly.

    Pass ``--summary`` to get the compact 6-bucket by_kind summary for the
    /work top card; combine with ``--include-all-personas`` for full
    visibility.
    """
    if summary:

        def action(c: MAPClient):
            return c.get_agent_work_summary(
                include_all_personas=include_all_personas,
                topics_limit=topics_limit,
                experiments_limit=experiments_limit,
            )

        _run(action, detect_deprecated=True)
        return

    def action(c: MAPClient):
        return c.get_agent_work(
            notification_limit=notification_limit,
            notification_category=notification_category,
        )

    _run(action, detect_deprecated=True)


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
        typer.echo(
            "Error: experiment complete now requires --metadata with deployment/test evidence "
            f"(accepted keys include: {keys}). Use --allow-missing-evidence only for explicit exceptions.\n"
            "  schema: docs/cli-schemas.md#experiment-complete-metadata",
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
# Review verdict file — schema: sdk/python/map_types/schemas.py:ReviewVerdictFile
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


@app.command("status")
def project_or_global_status(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    def action(c: MAPClient):
        if project is not None or project_key is not None:
            pid = _resolve_project(c, project, project_key)
            return c.get_project_status(pid)
        me = c.get_me()
        if me.role == AgentRole.admin:
            return c.get_global_status()
        pid = _resolve_project(c, None, None)
        return c.get_project_status(pid)

    _run(action)


@app.command("dashboard")
def map_dashboard() -> None:
    """One-glance markdown overview: identity, open topics, experiments, todos.

    Aggregates multiple read-only API calls into a single human-friendly
    markdown snapshot. Unlike ``status`` (which returns the server's
    status_md narrative) or ``work`` (which returns structured YAML for
    wakers), ``dashboard`` renders a compact, scannable view designed for
    humans who want to see "what's going on" without running several
    commands.

    Data is fetched live on each invocation — no stale snapshots.
    """
    from datetime import datetime

    from map_types import ExperimentPhase, TopicStatus

    try:
        ctx = _client_ctx()
        with ctx as client:
            me = client.get_me()
            project_id = _resolve_project(client, None, None)
            open_topics = client.list_topics(project_id, status=TopicStatus.open)
            experiments = client.list_experiments(project_id)
            todos = client.get_todos()
    except MAPHTTPError as exc:
        _emit_maphttp_error(exc, experiment_id=None, output_format=_cli_options.get("format", "yaml"))
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    persona = _cli_options.get("persona") or "default"
    lines: list[str] = []
    lines.append(f"# MAP Dashboard — {me.name}")
    lines.append(f"_Generated: {now} (persona: {persona})_")
    lines.append("")

    # --- Open Topics ---
    lines.append(f"## Open Topics ({len(open_topics)})")
    if open_topics:
        lines.append("| # | Title | Round | Comments | Experiments | Creator | Created |")
        lines.append("|---|-------|-------|----------|-------------|---------|---------|")
        for i, t in enumerate(open_topics, 1):
            title = (t.title or "").replace("|", "\\|")
            if len(title) > 60:
                title = title[:57] + "..."
            creator = (t.creator_name or "-").replace("|", "\\|")
            created = (t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "-")
            round_label = t.discussion_round.value if hasattr(t.discussion_round, "value") else str(t.discussion_round)
            lines.append(
                f"| {i} | {title} | {round_label} | {t.comment_count} | "
                f"{t.experiment_count} | {creator} | {created} |"
            )
    else:
        lines.append("_(none)_")
    lines.append("")

    # --- Experiments by phase ---
    active_phases = {
        ExperimentPhase.draft,
        ExperimentPhase.review,
        ExperimentPhase.approved,
        ExperimentPhase.running,
        ExperimentPhase.result_review,
    }
    active_exps = [e for e in experiments if e.phase in active_phases]
    done_exps = [e for e in experiments if e.phase == ExperimentPhase.done]
    cancelled_exps = [e for e in experiments if e.phase == ExperimentPhase.cancelled]

    lines.append(f"## Experiments ({len(active_exps)} active / {len(done_exps)} done / {len(cancelled_exps)} cancelled)")
    if active_exps:
        lines.append("| # | Title | Phase | Plan v | Topic | Updated |")
        lines.append("|---|-------|-------|--------|-------|---------|")
        for i, e in enumerate(active_exps, 1):
            title = (e.title or "").replace("|", "\\|")
            if len(title) > 50:
                title = title[:47] + "..."
            updated = (e.updated_at.strftime("%Y-%m-%d %H:%M") if e.updated_at else "-")
            topic = str(e.topic_id)[:8] + "…" if e.topic_id else "-"
            phase_label = e.phase.value if hasattr(e.phase, "value") else str(e.phase)
            lines.append(
                f"| {i} | {title} | {phase_label} | v{e.current_plan_version} | "
                f"{topic} | {updated} |"
            )
    else:
        lines.append("_(no active experiments)_")
    lines.append("")

    # --- Todos summary ---
    todo_buckets = [
        ("pending_reviews", "Pending Reviews", todos.pending_reviews),
        ("pending_result_reviews", "Result Reviews", todos.pending_result_reviews),
        ("pending_topic_replies", "Topic Replies", todos.pending_topic_replies),
        ("pending_round_acks", "Round Acks", todos.pending_round_acks),
        ("pending_advance_rounds", "Advance Rounds", todos.pending_advance_rounds),
        ("stale_open_topics", "Stale Topics", todos.stale_open_topics),
        ("mentions", "Mentions", todos.mentions),
        ("action_items", "Action Items", todos.action_items),
    ]
    obligation_count = sum(len(items) for _, _, items in todo_buckets)
    lines.append(f"## Todos ({obligation_count} obligation items)")
    for _label, display, items in todo_buckets:
        if items:
            lines.append(f"- **{display}**: {len(items)}")
    if obligation_count == 0:
        lines.append("_(no pending obligations)_")
    lines.append("")

    # --- Contextual lists ---
    if todos.my_open_topics:
        lines.append(f"### My Open Topics ({len(todos.my_open_topics)})")
        for t in todos.my_open_topics:
            title = (t.title or "").replace("|", "\\|")
            if len(title) > 60:
                title = title[:57] + "..."
            round_label = t.discussion_round.value if hasattr(t.discussion_round, "value") else str(t.discussion_round)
            lines.append(f"- `{t.id}` — {title} ({round_label}, {t.comment_count} comments)")
        lines.append("")
    if todos.my_open_experiments:
        lines.append(f"### My Open Experiments ({len(todos.my_open_experiments)})")
        for e in todos.my_open_experiments:
            title = (e.title or "").replace("|", "\\|")
            if len(title) > 50:
                title = title[:47] + "..."
            phase_label = e.phase.value if hasattr(e.phase, "value") else str(e.phase)
            lines.append(f"- `{e.id}` — {title} ({phase_label})")
        lines.append("")

    typer.echo("\n".join(lines))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
