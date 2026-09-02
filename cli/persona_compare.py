"""Persona-compare rendering for ``map experiment status --persona-compare``.

Split out of ``cli/main.py`` (size-cap regression, see
tests/cli/test_compat.py::test_cli_main_py_under_size_cap). The
``cli.main`` module still re-exports every public name below so
existing imports (cli/commands/experiment.py lazy import, tests) keep
working unchanged.
"""

import uuid
from typing import Any

import typer
import yaml
from map_client.client import MAPClient

from cli.runner import _to_yamlable  # noqa: E402

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
    # Lazy import: ``_cli_options`` / ``_transport`` / ``_to_yamlable``
    # live on the ``cli.main`` module state; importing them lazily keeps
    # this module importable from cli.main without a cycle.
    from cli.main import _transport  # runtime state (monkeypatch surface)
    from cli.project_context import current_context

    try:
        # 实验 e7244a91（A1/A3）：身份配置经 ProjectContext 单点解析
        # （显式 --config-root 时随配置根）。
        config = current_context().config
    except ValueError as exc:
        typer.echo(
            "Error: --persona-compare needs .map/agents.local.yaml; "
            "run `map bootstrap` first.",
            err=True,
        )
        raise typer.Exit(2) from exc

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
