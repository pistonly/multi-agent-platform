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
from map_client.exceptions import MAPConflictError, MAPHTTPError, MAPNotFoundError
from map_client.project_config import find_map_dir, load_project_map_config, resolve_client

# arch experiment (0519e2a3) PR1: shared SDK umbrella. Later PRs move
# shared helpers here (see plan). The CLI must stay importable even
# when ``map_sdk`` is unavailable, so we don't gate startup on it.
from map_sdk.evidence import (
    EVIDENCE_METADATA_KEYS,
    metadata_has_completion_evidence,
)
from pydantic import BaseModel, ConfigDict

# arch experiment (0519e2a3) PR3: agent sub-app split. Imported only to
# register ``agent_app`` below — the helpers the sub-app uses
# (``_run``, ``_client_ctx``, …) are pulled in lazily inside each
# command body to break the ``cli.main ↔ cli.commands.agent`` cycle.
from cli.commands.agent import agent_app
from server.domain.models import AgentRole
from server.domain.schemas import (
    ExperimentComplete,
    ExperimentCreate,
    ExperimentLogCreate,
    ExperimentResultDecision,
    PlanInput,
    PlanRevise,
    ProjectStatusRevise,
    ReviewCreate,
    TopicAdvanceRound,
    TopicResolve,
)

app = typer.Typer(name="map", help="Multi-Agent Platform CLI", rich_markup_mode=None)
project_app = typer.Typer(help="Project commands")
experiment_app = typer.Typer(help="Experiment commands", rich_markup_mode=None)
persona_app = typer.Typer(help="Persona / identity commands")
runtime_app = typer.Typer(help="Agent runtime session commands")
# arch experiment (0519e2a3) PR3: agent sub-app split. Lives in
# cli/commands/agent.py and is imported here only to register the
# sub-app — the helpers it uses (_run, _client_ctx, …) are referenced
# via lazy imports inside each command body to break the cycle
# ``cli.main ↔ cli.commands.agent``.
app.add_typer(project_app, name="project")
app.add_typer(experiment_app, name="experiment")
app.add_typer(persona_app, name="persona")
app.add_typer(runtime_app, name="runtime")
app.add_typer(agent_app, name="agent")

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
        return (0, _N2_DEFAULT_RELEASE_MINOR := 9) if False else (0, 9)
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
        help=(
            "Output format for error envelopes (I1(e) + 8a8822b5 (f)): "
            "'yaml' (default) renders the human-friendly Error/Hint/Escalation "
            "lines; 'json' emits the {error_code, message, hint, docs_url, "
            "retryable, recovery_command} envelope to stderr for scripts; "
            "'legacy' is an alias for 'yaml' and will be removed in N=2. "
            "Explicit --format always wins over the MAP_CLI_FORMAT env var."
        ),
    ),
) -> None:
    # 8a8822b5 (f): resolve --format / MAP_CLI_FORMAT priority.
    # Explicit --format flag > MAP_CLI_FORMAT env var > default 'yaml'.
    env_format = os.environ.get("MAP_CLI_FORMAT", "").strip().lower() or None
    resolved: str | None
    source: str
    if output_format is not None:
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

    if resolved not in ("yaml", "json"):
        typer.echo(
            f"Error: unknown --format {resolved!r}; expected 'yaml', 'json', or 'legacy'.",
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
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    try:
        yield client
    finally:
        client.close()


def _print_json(data: Any) -> None:
    typer.echo(yaml.safe_dump(_to_yamlable(data), allow_unicode=True, sort_keys=False))


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
        output_format: ``"yaml"`` (default) or ``"json"``. When ``"json"``,
            errors emit a structured ``{error_code, message, hint,
            retryable, recovery_command}`` envelope as JSON to stderr
            so scripts can parse it. When None, falls back to the global
            ``--format`` option (default ``yaml``).
    """
    if output_format is None:
        output_format = _cli_options.get("format", "yaml")
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
            _print_json(result)
    except MAPHTTPError as exc:
        _emit_maphttp_error(exc, experiment_id=experiment_id, output_format=output_format)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        if output_format == "json":
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
    typer.echo(envelope.model_dump_json(), err=True)


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


@persona_app.command("list")
def persona_list(
    project_root: Path | None = typer.Option(None, "--project-root"),
) -> None:
    """List personas defined in .map/agents.yaml."""
    map_dir = _require_map_dir(project_root)
    cfg = load_project_map_config(map_dir=map_dir)
    rows = []
    for key, info in cfg.personas.items():
        has_token = key in cfg.tokens
        rows.append(
            {
                "persona": key,
                "agent_name": info.agent_name,
                "has_token": has_token,
                "description": info.description,
            }
        )
    _print_json(rows)


@persona_app.command("whoami")
def persona_whoami(
    persona: str | None = typer.Option(None, "--persona", "-p"),
    project_root: Path | None = typer.Option(None, "--project-root"),
) -> None:
    """Show MAP identity for the selected persona (default from .map/config.yaml)."""
    if persona is not None:
        _cli_options["persona"] = persona
    if project_root is not None:
        _cli_options["project_root"] = project_root

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


@project_app.command("create")
def project_create(
    key: str = typer.Option(..., "--key"),
    name: str = typer.Option(..., "--name"),
    path: str = typer.Option(..., "--path"),
    description: str | None = typer.Option(None, "--description"),
) -> None:
    _run(lambda c: c.create_project(key, name, path, description))


@project_app.command("list")
def project_list(include_archived: bool = typer.Option(False, "--include-archived")) -> None:
    _run(lambda c: c.list_projects(include_archived=include_archived))


@project_app.command("decisions")
def project_decisions(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    limit: int = typer.Option(20, "--limit", min=1, max=100),
) -> None:
    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        return c.list_project_decisions(pid, limit=limit)

    _run(action)


status_app = typer.Typer(help="Project Current Status commands")
project_app.add_typer(status_app, name="status")


@status_app.command("revise")
def project_status_revise(
    status_file: Path = typer.Option(..., "--file"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    note: str | None = typer.Option(None, "--note"),
) -> None:
    payload = ProjectStatusRevise(content_md=_read_text_file(status_file, kind="status"), change_note=note)

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        return c.revise_project_status(pid, payload)

    _run(action)


@status_app.command("versions")
def project_status_versions(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    _run(lambda c: c.list_project_status_versions(_resolve_project(c, project, project_key)))


@status_app.command("show")
def project_status_show(
    version: int = typer.Option(..., "--version"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
) -> None:
    _run(
        lambda c: c.get_project_status_version(_resolve_project(c, project, project_key), version)
    )


@experiment_app.command("create")
def experiment_create(
    title: str = typer.Option(..., "--title"),
    plan_file: Path = typer.Option(..., "--plan-file"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    description: str | None = typer.Option(None, "--description"),
    submit_for_review: bool = typer.Option(False, "--submit-for-review"),
    topic_id: uuid.UUID | None = typer.Option(None, "--topic-id"),
    force_lint_bypass: bool = typer.Option(
        False,
        "--force-lint-bypass",
        help="Skip the local plan frontmatter lint pre-check (server still enforces it).",
    ),
) -> None:
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

    payload = ExperimentCreate(
        title=title,
        description=description,
        plan=PlanInput(content_md=content),
        submit_for_review=submit_for_review,
        topic_id=topic_id,
    )

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        return c.create_experiment(pid, payload)

    _run(action)


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
    from server.domain.models import ExperimentPhase

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

    _run(action)


@experiment_app.command("submit-review")
def experiment_submit_review(experiment_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    _run(lambda c: c.submit_for_review(experiment_id), experiment_id=experiment_id)


@experiment_app.command("approve")
def experiment_approve(experiment_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    _run(lambda c: c.approve_experiment(experiment_id), experiment_id=experiment_id)


@experiment_app.command("start")
def experiment_start(experiment_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    _run(lambda c: c.start_experiment(experiment_id), experiment_id=experiment_id)


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
            f"(accepted keys include: {keys}). Use --allow-missing-evidence only for explicit exceptions.",
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
            f"Error: invalid --review-verdict-file {path}: {exc}",
            err=True,
        )
        raise typer.Exit(2) from None


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
) -> None:
    """Submit experiment result for reviewer approval (running → result_review).

    The log body must follow the 4-段 template contract (summary / 实施 log /
    风险 / acceptance). Soft validation runs server-side; any
    ``template_validation.warnings`` are surfaced to stderr (one
    ``[WARN] template: <code>`` line each) and to the stdout YAML/JSON
    payload under ``template_validation``. Warnings never block the
    transition — add a follow-up log to address them.
    """
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
) -> None:
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
) -> None:
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
    experiment_id = _require_option_uuid(experiment_id)
    from server.domain.schemas import ExperimentUpdate

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


# --- execution lock (CP-3) ------------------------------------------------


lock_app = typer.Typer(help="Experiment execution lock commands (per-project).")
experiment_app.add_typer(lock_app, name="lock")


@lock_app.command("acquire")
def experiment_lock_acquire(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    ttl: int = typer.Option(1800, "--ttl", min=1, help="Lock TTL in seconds."),
) -> None:
    _run(lambda c: c.acquire_experiment_lock(experiment_id, ttl_seconds=ttl), experiment_id=experiment_id)


@lock_app.command("release")
def experiment_lock_release(experiment_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    _run(lambda c: c.release_experiment_lock(experiment_id), experiment_id=experiment_id)


@lock_app.command("force-release")
def experiment_lock_force_release(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    reason: str = typer.Option(..., "--reason"),
    actor: str | None = typer.Option(None, "--actor"),
) -> None:
    _run(lambda c: c.force_release_experiment_lock(experiment_id, reason=reason, actor=actor), experiment_id=experiment_id)


@lock_app.command("skip")
def experiment_lock_skip(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    next_attempt_at: str = typer.Option(..., "--next-attempt-at"),
) -> None:
    _run(lambda c: c.record_experiment_lock_skip(experiment_id, next_attempt_at=next_attempt_at), experiment_id=experiment_id)


@lock_app.command("scan-stalled")
def experiment_lock_scan_stalled() -> None:
    """Scan running experiment locks and emit no-progress notifications."""

    _run(lambda c: c.scan_stalled_experiment_locks())


review_app = typer.Typer(help="Review commands")
experiment_app.add_typer(review_app, name="review")


@review_app.command("add")
def review_add(
    experiment_id: uuid.UUID = typer.Option(..., "--id"),
    review_file: Path = typer.Option(..., "--review"),
) -> None:
    raw = yaml.safe_load(_read_text_file(review_file, kind="review"))
    payload = ReviewCreate.model_validate(raw)
    _run(lambda c: c.create_review(experiment_id, payload), experiment_id=experiment_id)


plan_app = typer.Typer(help="Plan commands")
experiment_app.add_typer(plan_app, name="plan")


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
    from server.domain.models import CommentAnchorType
    from server.domain.schemas import CommentCreate

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


notification_app = typer.Typer(help="In-app notification commands")
app.add_typer(notification_app, name="notification")


@notification_app.command("list")
def notification_list(
    unread_only: bool = typer.Option(False, "--unread-only"),
    category: str | None = typer.Option(None, "--category", help="wakeable|digest|all"),
    target_type: str | None = typer.Option(None, "--target-type"),
    limit: int = typer.Option(50, "--limit"),
    offset: int = typer.Option(0, "--offset"),
) -> None:
    _run(
        lambda c: c.list_notifications(
            unread_only=unread_only,
            category=category,
            target_type=target_type,
            limit=limit,
            offset=offset,
        )
    )


@notification_app.command("read")
def notification_read(notification_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    _run(lambda c: c.mark_notification_read(notification_id))


@notification_app.command("read-all")
def notification_read_all() -> None:
    _run(lambda c: c.mark_all_notifications_read())


inbound_event_app = typer.Typer(help="Runtime-waker inbound event commands (D6 server gate)")
app.add_typer(inbound_event_app, name="inbound-event")


@inbound_event_app.command("record")
def inbound_event_record(
    event_id: uuid.UUID = typer.Option(..., "--event-id", help="Upstream notification id (UUID)."),
    fingerprint: str = typer.Option(
        ..., "--fingerprint", help="Dedup key (server enforces UNIQUE per agent)."
    ),
    event_type: str = typer.Option(
        ..., "--event-type", help="Logical event type (e.g. mention, pending_review, topic_lifecycle)."
    ),
    source: str = typer.Option(
        "polling", "--source", help="polling|sse|replay (Phase 1 = polling)."
    ),
    payload_file: Path | None = typer.Option(
        None, "--payload-file", help="Optional JSON file with extra payload fields."
    ),
) -> None:
    """Record that the caller is about to act on ``event_id``.

    Thin wrapper for ``POST /agents/me/inbound-events``. On 409 the CLI exits
    with a non-zero status and prints the server detail — the waker treats
    409 as "already woken" and skips resume.
    """
    from map_types.enums import InboundEventSource

    from server.domain.schemas import InboundEventCreate

    extra_payload: dict | None = None
    if payload_file is not None:
        import json

        extra_payload = json.loads(_read_text_file(payload_file, kind="payload"))
    payload = InboundEventCreate(
        event_id=event_id,
        event_type=event_type,
        source=InboundEventSource(source),
        fingerprint=fingerprint,
        payload=extra_payload,
    )

    def action(c: MAPClient):
        try:
            return c.record_inbound_event(payload)
        except MAPConflictError as exc:
            typer.echo(
                f"inbound-event duplicate (409): {exc.detail}",
                err=True,
            )
            raise typer.Exit(2) from exc

    _run(action)


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


audit_app = typer.Typer(help="Audit log commands (admin)")
app.add_typer(audit_app, name="audit")


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
    # Bypass the shared ``_run`` wrapper so the (rows, total) tuple can be
    # rendered as a header line + YAML rows rather than a raw tuple. The
    # shared wrapper would YAML-dump the tuple as ``[rows, total]`` which
    # is the wrong shape for an admin greppable timeline.
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


topic_app = typer.Typer(help="Topic commands", rich_markup_mode=None)
app.add_typer(topic_app, name="topic")

mention_app = typer.Typer(help="Mention todo commands")
app.add_typer(mention_app, name="mention")


@mention_app.command("dismiss")
def mention_dismiss(
    mention_id: uuid.UUID = typer.Option(..., "--id", help="Mention UUID from `map todos`."),
) -> None:
    """Dismiss one @mention for the current persona (removes it from `map todos`).

    Idempotent: dismissing an already-dismissed mention returns the same result.
    """
    _run(lambda c: c.dismiss_mention(mention_id))


@mention_app.command("list")
def mention_list() -> None:
    """List open @mentions for the current persona."""
    _run(lambda c: c.get_todos().mentions)


@mention_app.command("dismiss-all")
def mention_dismiss_all() -> None:
    """Dismiss all open @mentions for the current persona."""
    _run(lambda c: c.dismiss_all_mentions())


@mention_app.command("reconcile-stale")
def mention_reconcile_stale() -> None:
    """Admin stub: offline stale mention reconciliation (T1 D5 MVP — not implemented)."""
    typer.echo(
        "mention reconcile-stale: stub only — stale mentions are filtered in "
        "topic-progress/todos projection; use write-path dismiss on comment."
    )


todo_app = typer.Typer(help="Todo partition clear routing (explicit_only buckets)")
app.add_typer(todo_app, name="todo")


@todo_app.command("clear")
def todo_clear(
    key: str = typer.Option(..., "--key", help="Work-item idempotency_key or partition id"),
) -> None:
    """Route explicit_only todo partitions to the canonical clear CLI (T1 D7)."""
    if key.startswith("notification:"):
        notification_id = uuid.UUID(key.split(":", 1)[1])
        _run(lambda c: c.mark_notification_read(notification_id))
        return
    if key.startswith("action_item:"):
        item_id = uuid.UUID(key.split(":", 1)[1])
        _run(lambda c: c.complete_action_item(item_id))
        return
    if key.startswith("my_open_topics:") or key.startswith("topic:"):
        topic_id = uuid.UUID(key.rsplit(":", 1)[-1])
        _run(lambda c: c.dismiss_topic(topic_id))
        return
    if key.startswith("unread_change:"):
        topic_id = uuid.UUID(key.split(":", 2)[1])
        _run(lambda c: c.mark_topic_read(topic_id))
        return
    raise typer.BadParameter(
        f"unsupported todo clear key {key!r}; explicit_only: notification, action_item, "
        "my_open_topics, unread_change"
    )


@topic_app.command("create")
def topic_create(
    title: str = typer.Option(..., "--title"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    description: str | None = typer.Option(None, "--description"),
) -> None:
    from server.domain.schemas import TopicCreate

    payload = TopicCreate(title=title, description=description)

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        return c.create_topic(pid, payload)

    _run(action)


@topic_app.command("list")
def topic_list(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    status: str | None = typer.Option(None, "--status"),
    creator: str | None = typer.Option(
        None,
        "--creator",
        help="Filter by topic creator. Accepts agent_name (current project) or agent_id UUID; "
        "alias for --creator-agent-id.",
    ),
    creator_agent_id: uuid.UUID | None = typer.Option(
        None,
        "--creator-agent-id",
        help="Filter by creator agent_id UUID. Use --creator for name-or-id shorthand.",
    ),
    q: str | None = typer.Option(None, "--q"),
    page: int = typer.Option(1, "--page", min=1),
    page_size: int = typer.Option(100, "--page-size", min=1, max=100),
    include_archived: bool = typer.Option(False, "--include-archived"),
) -> None:
    from server.domain.models import TopicStatus

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        st = TopicStatus(status) if status else None
        resolved_creator_id = _resolve_creator_agent_id(c, pid, creator, creator_agent_id)
        return c.list_topics(
            pid,
            status=st,
            creator_agent_id=resolved_creator_id,
            q=q,
            page=page,
            page_size=page_size,
            include_archived=include_archived,
        )

    _run(action)


@topic_app.command("show")
def topic_show(topic_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    _run(lambda c: c.get_topic(topic_id))


@topic_app.command("progress")
def topic_progress() -> None:
    """Per-agent topic work items view (obligation + contextual); same source as todos topic buckets."""
    _run(lambda c: c.get_topic_progress())


@topic_app.command("resolve")
def topic_resolve(
    topic_id: uuid.UUID = typer.Option(..., "--id"),
    resolve_file: Path = typer.Option(..., "--file"),
) -> None:
    payload = _load_topic_resolve_payload(resolve_file)
    _run(lambda c: c.resolve_topic(topic_id, payload))


@topic_app.command("advance-round")
def topic_advance_round(
    topic_id: uuid.UUID = typer.Option(..., "--id"),
    increment_summary: bool = typer.Option(
        True,
        "--increment-summary/--no-increment-summary",
        help="Increment round_summary_count before advancing.",
    ),
    ack_ids: str | None = typer.Option(
        None,
        "--ack-ids",
        help="Host: comma-separated participant agent UUIDs already acknowledged.",
    ),
    ack: str | None = typer.Option(
        None,
        "--ack",
        help="Participant: accept, reject, or dismiss acknowledgement for the current round.",
    ),
) -> None:
    acknowledged_by: list[uuid.UUID] = []
    if ack_ids:
        acknowledged_by = [uuid.UUID(item.strip()) for item in ack_ids.split(",") if item.strip()]
    payload = TopicAdvanceRound(
        increment_summary=increment_summary,
        acknowledged_by=acknowledged_by,
        ack=ack,  # type: ignore[arg-type]
    )
    _run(lambda c: c.advance_topic_round(topic_id, payload))


@topic_app.command("comment")
def topic_comment(
    topic_id: uuid.UUID = typer.Option(..., "--id"),
    body: str | None = typer.Option(None, "--body"),
    body_file: Path | None = typer.Option(None, "--file"),
    parent: uuid.UUID | None = typer.Option(None, "--parent"),
) -> None:
    from server.domain.schemas import TopicCommentCreate

    if body is None and body_file is None:
        typer.echo("Error: either --body or --file is required", err=True)
        raise typer.Exit(2)
    if body is not None and body_file is not None:
        typer.echo("Error: use only one of --body or --file", err=True)
        raise typer.Exit(2)
    content = body if body is not None else _read_text_file(body_file, kind="comment")
    payload = TopicCommentCreate(body=content, parent_id=parent)
    _run(lambda c: c.create_topic_comment(topic_id, payload))


@topic_app.command("close")
def topic_close(topic_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    _run(lambda c: c.close_topic(topic_id))


@topic_app.command("reopen")
def topic_reopen(topic_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    _run(lambda c: c.reopen_topic(topic_id))


@topic_app.command("dismiss")
def topic_dismiss(topic_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    """Hide an open topic from host todos until new activity (same as Web UI ✕)."""
    _run(lambda c: c.dismiss_topic(topic_id))


@topic_app.command("read")
def topic_read(topic_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    """Mark contextual unread changes as seen; obligations still require reply/ack/mention handling."""
    _run(lambda c: c.mark_topic_read(topic_id))


@topic_app.command("mark-seen")
def topic_mark_seen(topic_id: uuid.UUID = typer.Option(..., "--id")) -> None:
    """Alias of topic read: clears contextual unread only, not reply/ack/mention obligations."""
    _run(lambda c: c.mark_topic_read(topic_id))


@topic_app.command(
    "archive",
    epilog="Use --undo or --unarchive to restore an archived topic.",
)
def topic_archive(
    topic_id: uuid.UUID | None = typer.Option(None, "--id", help="Topic UUID."),
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
    """Archive (or unarchive) a topic.

    Thin wrapper around ``PATCH /topics/{id}`` with ``archived=true`` (or
    ``false`` when ``--undo``/``--unarchive`` is set). Archive hides the topic
    from ``topic list`` by default but ``topic show`` still returns it
    including ``archived_at``. Archive is reversible — re-run with ``--undo``
    to restore.

    Examples:

        # Archive
        map --persona host topic archive --id <uuid>

        # Unarchive (two equivalent spellings)
        map --persona host topic archive --id <uuid> --undo
        map --persona host topic archive --id <uuid> --unarchive
    """
    topic_id = _require_option_uuid(topic_id)
    from server.domain.schemas import TopicUpdate

    payload = TopicUpdate(archived=not (undo or unarchive))
    object_kind = "topic"

    def action(c: MAPClient):
        try:
            return c.update_topic(topic_id, payload)
        except MAPNotFoundError as exc:
            typer.echo(
                f"Error: {object_kind} {topic_id} not found",
                err=True,
            )
            raise typer.Exit(1) from exc

    _run(action)


action_app = typer.Typer(help="Topic action item commands")
app.add_typer(action_app, name="action")


@action_app.command("list")
def action_list(
    project: uuid.UUID | None = typer.Option(None, "--project"),
    project_key: str | None = typer.Option(None, "--project-key"),
    owner_agent_id: uuid.UUID | None = typer.Option(None, "--owner-agent-id"),
    mine: bool = typer.Option(False, "--mine", help="Only action items assigned to the current agent."),
    status: str | None = typer.Option("open", "--status"),
    limit: int = typer.Option(100, "--limit", min=1, max=200),
) -> None:
    from map_types.enums import TopicActionItemStatus

    def action(c: MAPClient):
        pid = _resolve_project(c, project, project_key)
        owner = owner_agent_id
        if mine:
            owner = c.get_me().id
        status_filter = TopicActionItemStatus(status) if status else None
        return c.list_project_action_items(
            pid,
            owner_agent_id=owner,
            status=status_filter,
            limit=limit,
        )

    _run(action)


@action_app.command("complete")
def action_complete(
    action_item_id: uuid.UUID = typer.Option(..., "--id", help="Action item UUID to mark done."),
) -> None:
    """Close an action item as done (open -> done)."""

    def action(c: MAPClient):
        return c.complete_action_item(action_item_id)

    _run(action)


@action_app.command("deliver")
def action_deliver(
    action_item_id: uuid.UUID = typer.Option(..., "--id", help="Action item UUID to deliver."),
) -> None:
    """Deliver an open action item (source topic may be closed/archived)."""

    def action(c: MAPClient):
        return c.deliver_action_item(action_item_id)

    _run(action)


@action_app.command("cancel")
def action_cancel(
    action_item_id: uuid.UUID = typer.Option(..., "--id", help="Action item UUID to cancel."),
    reason: str = typer.Option(..., "--reason", help="Cancellation reason (length-validated by category)."),
    category: str | None = typer.Option(
        None,
        "--category",
        help="implementation | decision | unspecified (default). Affects reason length threshold.",
    ),
) -> None:
    """Close an action item as cancelled (open -> cancelled)."""

    from map_types.enums import ActionItemCategory
    from map_types.schemas import ActionItemCancel

    def action(c: MAPClient):
        cat = ActionItemCategory(category) if category else None
        payload = ActionItemCancel(reason=reason, category=cat)
        return c.cancel_action_item(action_item_id, payload)

    _run(action)


@action_app.command("link")
def action_link(
    action_item_id: uuid.UUID = typer.Option(..., "--id", help="Action item UUID to link."),
    experiment_id: uuid.UUID = typer.Option(
        ...,
        "--experiment-id",
        help="Experiment UUID to attach to the action item (must share project).",
    ),
) -> None:
    """Attach an experiment to an open action item so future experiment
    ``done`` cascades the action item automatically."""

    def action(c: MAPClient):
        return c.link_action_item(action_item_id, experiment_id)

    _run(action)


@action_app.command("mark-wake-sent")
def action_mark_wake_sent(
    action_item_id: uuid.UUID = typer.Option(..., "--id", help="Action item UUID to wake."),
) -> None:
    """Bump wake_count + stamp last_woken_at + write ``action_item.wake_sent``
    audit row. Used by the runtime-waker CLI to advance the three-stage
    escalation timeline (experiment B / I4). Owner or admin only."""

    def action(c: MAPClient):
        return c.mark_wake_sent(action_item_id)

    _run(action)


@action_app.command("mark-stale")
def action_mark_stale(
    action_item_id: uuid.UUID = typer.Option(..., "--id", help="Action item UUID to mark stale."),
) -> None:
    """Stamp stale_at + write the ``action_item.stale`` audit row after the
    4th unanswered wake. Admin only (system escalation, experiment B / I4)."""

    def action(c: MAPClient):
        return c.mark_stale(action_item_id)

    _run(action)


@runtime_app.command("chat")
def runtime_chat(
    persona: str = typer.Option(
        "host",
        "--persona",
        "-p",
        help="Persona whose runtime session to resume (default: host)",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="Repo root containing .map/ (default: search upward from cwd)",
    ),
    state_file: Path | None = typer.Option(
        None,
        "--state-file",
        help="Runtime waker state file (default: .map/runtime-waker-state-<persona>.json)",
    ),
    runtime_home: Path | None = typer.Option(
        None,
        "--runtime-home",
        help="Claude runtime HOME (default: .map/claude-runtime-home-<persona>)",
    ),
    session_id: str | None = typer.Option(
        None,
        "--session-id",
        help="Resume a specific Claude session id (default: read from state file)",
    ),
    prompt: str | None = typer.Option(
        None,
        "--prompt",
        help="Send one prompt before entering interactive REPL",
    ),
    new_session: bool = typer.Option(
        False,
        "--new-session",
        help="Start a fresh Claude session instead of resuming state",
    ),
    ignore_waker: bool = typer.Option(
        False,
        "--ignore-waker",
        help="Allow chat while map-runtime-waker is running (may conflict)",
    ),
    model: str | None = typer.Option(None, "--model", help="Optional Claude model override"),
) -> None:
    """Resume a persona runtime session and chat interactively from the terminal."""
    from cli.runtime_chat import run_runtime_chat

    run_runtime_chat(
        persona=persona,
        project_root=project_root or _cli_options.get("project_root"),
        state_file=state_file,
        runtime_home=runtime_home,
        session_id=session_id,
        new_session=new_session,
        initial_prompt=prompt,
        ignore_waker=ignore_waker,
        model=model,
    )


@runtime_app.command("status")
def runtime_status(
    persona: str = typer.Option(
        "host",
        "--persona",
        "-p",
        help="Persona to inspect (default: host)",
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="Repo root containing .map/ (default: search upward from cwd)",
    ),
    state_file: Path | None = typer.Option(
        None,
        "--state-file",
        help="Runtime waker state file (default: .map/runtime-waker-state-<persona>.json)",
    ),
) -> None:
    """Show resumable session id and whether runtime waker is running."""
    from cli.runtime_chat import dump_runtime_chat_status

    _print_json(
        dump_runtime_chat_status(
            persona=persona,
            project_root=project_root or _cli_options.get("project_root"),
            state_file=state_file,
        )
    )


# 8a8822b5 (d): docs sub-app + `map docs error-codes` CLI. Reads the
# machine-readable index at ``docs/error-codes/index.json`` (repo root)
# and renders it as YAML or filtered view via ``--search``. No MAP
# client / API call required — docs are bundled with the repo.
_ERROR_CODES_INDEX_RELATIVE = "docs/error-codes/index.json"

_ERROR_CODES_SEARCH_FIELDS: tuple[str, ...] = (
    "code",
    "category",
    "title",
    "description",
    "hint",
    "docs_url",
    "owner_experiment",
    "since_version",
)


def _resolve_repo_root(start: Path | None = None) -> Path:
    """Walk upward from ``start`` (or cwd) until a directory containing
    ``docs/error-codes/index.json`` is found. Falls back to the start
    directory if not found, so the loader can produce a clean error.
    """
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / _ERROR_CODES_INDEX_RELATIVE).is_file():
            return candidate
    return current


def _load_error_codes_index(
    project_root: Path | None = None,
) -> tuple[Path, dict[str, Any]]:
    repo_root = _resolve_repo_root(project_root or _cli_options.get("project_root"))
    index_path = repo_root / _ERROR_CODES_INDEX_RELATIVE
    if not index_path.is_file():
        typer.echo(
            f"Error: error codes index not found at {index_path}. "
            "Run from the repo root or pass --project-root.",
            err=True,
        )
        raise typer.Exit(2)
    try:
        raw = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        typer.echo(f"Error: failed to parse {index_path}: {exc}", err=True)
        raise typer.Exit(2) from None
    if not isinstance(raw, dict) or not isinstance(raw.get("codes"), list):
        typer.echo(
            f"Error: {index_path} must be a JSON object with a 'codes' list.",
            err=True,
        )
        raise typer.Exit(2)
    return index_path, raw


def _match_error_code(code_entry: dict[str, Any], keywords: list[str]) -> bool:
    if not keywords:
        return True
    haystack_parts: list[str] = []
    for field in _ERROR_CODES_SEARCH_FIELDS:
        value = code_entry.get(field)
        if value is None:
            continue
        haystack_parts.append(str(value))
    haystack = "\n".join(haystack_parts).lower()
    return all(kw.lower() in haystack for kw in keywords)


def _search_keywords_to_list(raw: list[str] | None) -> list[str]:
    """Normalize --search repeats: split comma-separated, strip, drop empties,
    preserve order, dedupe while preserving order.
    """
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        for piece in str(item).split(","):
            kw = piece.strip()
            if not kw or kw in seen:
                continue
            seen.add(kw)
            out.append(kw)
    return out


docs_app = typer.Typer(help="Repo-bundled documentation commands (no API).")
app.add_typer(docs_app, name="docs")


@docs_app.command("error-codes")
def docs_error_codes(
    search: list[str] | None = typer.Option(
        None,
        "--search",
        help=(
            "Keyword filter (case-insensitive substring match against "
            "code / category / title / description / hint / docs_url / "
            "owner_experiment / since_version). Repeat for AND of "
            "multiple keywords, or comma-separate in one value."
        ),
    ),
    project_root: Path | None = typer.Option(
        None,
        "--project-root",
        help="Repo root containing docs/error-codes/index.json (default: walk up from cwd).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit the raw index.json payload (machine-readable) instead of the default YAML table.",
    ),
) -> None:
    """List MAP error codes from ``docs/error-codes/index.json``."""
    index_path, payload = _load_error_codes_index(project_root)
    keywords = _search_keywords_to_list(search)
    codes = payload.get("codes") or []
    matched = [entry for entry in codes if isinstance(entry, dict) and _match_error_code(entry, keywords)]

    if as_json:
        if keywords:
            _print_json({"schema_version": payload.get("schema_version"), "codes": matched})
        else:
            _print_json(payload)
        return

    schema_version = payload.get("schema_version")
    generated_at = payload.get("generated_at")
    owner_experiment = payload.get("owner_experiment")
    header_lines = [
        f"# error codes index: {index_path}",
        f"# schema_version: {schema_version}",
        f"# generated_at: {generated_at}",
        f"# owner_experiment: {owner_experiment}",
    ]
    if keywords:
        header_lines.append(f"# search: {', '.join(keywords)} (AND)")
    header_lines.append(f"# matched: {len(matched)}/{len(codes)}")
    typer.echo("\n".join(header_lines))
    if not matched:
        return
    table_rows: list[list[str]] = []
    for entry in matched:
        table_rows.append(
            [
                str(entry.get("code", "")),
                str(entry.get("category", "")),
                str(entry.get("http_status", "")),
                str(entry.get("title", "")),
                str(entry.get("owner_experiment", "")),
            ]
        )
    header = ["code", "category", "http", "title", "owner_experiment"]
    widths = [max(len(h), *(len(row[i]) for row in table_rows)) for i, h in enumerate(header)]
    sep = "-+-".join("-" * w for w in widths)
    typer.echo(" | ".join(h.ljust(widths[i]) for i, h in enumerate(header)))
    typer.echo(sep)
    for row in table_rows:
        typer.echo(" | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


feedback_app = typer.Typer(help="Platform feedback inbox commands")
app.add_typer(feedback_app, name="feedback")


@feedback_app.command("submit")
def feedback_submit(
    body: str | None = typer.Option(None, "--body", help="Feedback text (free-form)."),
    body_file: Path | None = typer.Option(
        None,
        "--file",
        help="Read feedback body from a file (avoids shell-quoting issues with backticks / $vars).",
    ),
    category: str | None = typer.Option(
        None, "--category", help="bug|suggestion|question|other (optional, admin triage hint)"
    ),
    project: uuid.UUID | None = typer.Option(
        None, "--project", help="Source project context (optional)"
    ),
) -> None:
    from map_types.enums import FeedbackCategory

    from server.domain.schemas import PlatformFeedbackCreate

    if body is None and body_file is None:
        typer.echo("Error: either --body or --file is required", err=True)
        raise typer.Exit(2)
    if body is not None and body_file is not None:
        typer.echo("Error: use only one of --body or --file", err=True)
        raise typer.Exit(2)
    content = body if body is not None else _read_text_file(body_file, kind="feedback")
    payload = PlatformFeedbackCreate(
        body=content,
        project_id=project,
        category=FeedbackCategory(category) if category else None,
    )
    _run(lambda c: c.submit_feedback(payload))


@feedback_app.command("list")
def feedback_list(
    status: str | None = typer.Option(None, "--status"),
    category: str | None = typer.Option(None, "--category"),
    project: uuid.UUID | None = typer.Option(None, "--project"),
    page: int = typer.Option(1, "--page", min=1),
    page_size: int = typer.Option(50, "--page-size", min=1, max=200),
    include_archived: bool = typer.Option(False, "--include-archived"),
) -> None:
    from map_types.enums import FeedbackCategory, FeedbackStatus

    def action(c: MAPClient):
        items, total = c.list_feedback_page(
            status=FeedbackStatus(status) if status else None,
            category=FeedbackCategory(category) if category else None,
            project_id=project,
            page=page,
            page_size=page_size,
            include_archived=include_archived,
        )
        return {"items": items, "total": total}

    _run(action, admin=True)


@feedback_app.command("get")
def feedback_get(feedback_id: uuid.UUID = typer.Argument(..., help="Feedback UUID")) -> None:
    _run(lambda c: c.get_feedback(feedback_id), admin=True)


@feedback_app.command("update")
def feedback_update(
    feedback_id: uuid.UUID = typer.Argument(..., help="Feedback UUID"),
    status: str | None = typer.Option(None, "--status"),
    category: str | None = typer.Option(None, "--category"),
    archived: bool | None = typer.Option(None, "--archived/--no-archived"),
) -> None:
    from map_types.enums import FeedbackCategory, FeedbackStatus

    from server.domain.schemas import PlatformFeedbackUpdate

    payload = PlatformFeedbackUpdate(
        status=FeedbackStatus(status) if status else None,
        category=FeedbackCategory(category) if category else None,
        archived=archived,
    )
    _run(lambda c: c.update_feedback(feedback_id, payload), admin=True)
def main() -> None:
    app()


if __name__ == "__main__":
    main()
