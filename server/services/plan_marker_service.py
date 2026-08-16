"""a764abf6 I1.(a) — plan frontmatter marker validator.

Provides two entry points:

* :func:`validate_plan_frontmatter` — soft validator returning a
  :class:`PlanMarkerValidationResult`. ``valid`` is always True. Used by
  the CLI ``plan validate`` command (plan (c)) and by read-only
  diagnostics where failure must not block.

* :func:`assert_plan_frontmatter_ok` — hard validator raising
  :class:`StateTransitionError` with ``error_code=STATE_MACHINE_PLAN_MARKER_MISSING``
  + a ``hint`` listing the missing fields when validation fails. Wired
  into the ``plan_revise`` and ``create_experiment`` paths per plan (a)
  acceptance so missing required fields block the save with a 422.

The 4 required frontmatter fields (markdown fenced ``---`` YAML block):

* ``title`` — non-empty string
* ``acceptance`` — non-empty list or non-empty string
* ``evidence_keys`` — non-empty list or non-empty string
* ``dependencies`` — list (empty list ``[]`` means "no dependencies",
  v0.12 M55C) or non-empty string
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import yaml

from server.services.errors import StateTransitionError

PlanMarkerWarningCode = Literal[
    "PLAN_MARKER_MISSING_FIELD",
    "PLAN_MARKER_EMPTY_LIST",
    "PLAN_MARKER_PARSE_ERROR",
    "PLAN_MARKER_FRONT_MATTER_MISSING",
]


@dataclass(frozen=True)
class PlanMarkerWarning:
    code: PlanMarkerWarningCode
    field: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class PlanMarkerValidationResult:
    warnings: list[PlanMarkerWarning] = field(default_factory=list)
    fields_present: tuple[str, ...] = ()
    frontmatter: dict[str, object] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return True


REQUIRED_PLAN_FIELDS: tuple[str, ...] = (
    "title",
    "acceptance",
    "evidence_keys",
    "dependencies",
)

_FRONT_MATTER_RE = re.compile(
    r"\A\s*---\s*\n(?P<body>.*?)\n---\s*(?:\n|\Z)",
    re.DOTALL,
)


def _frontmatter_template_hint(missing: tuple[str, ...] | list[str] = ()) -> str:
    """v0.12 M55B (E3/E4): copy-pasteable frontmatter template snippet.

    The hint is consumed by agents (CLI stderr / experiment logs), so it
    stays a compact fenced block (~15 lines max, review r2) — enough to
    copy verbatim, short enough not to drown the field-level diagnostics.
    """
    suffix = f" (missing: {', '.join(missing)})" if missing else ""
    return (
        f"Plan must begin with a YAML frontmatter block{suffix}. "
        "Copy this template and fill it in:\n"
        "---\n"
        'title: "实验标题"\n'
        "acceptance:\n"
        '  - "可验证的验收标准（每条一句）"\n'
        "evidence_keys:\n"
        '  - "pytest_summary"\n'
        "dependencies: []  # 无依赖写 []（v0.12 起）；有依赖列实验 UUID\n"
        "---"
    )


def _extract_frontmatter(content_md: str | None) -> tuple[str | None, dict[str, object]]:
    """Return ``(raw_yaml_or_None, parsed_dict)``.

    * ``(None, {})`` — no frontmatter block detected.
    * ``(raw, {})`` — frontmatter block present but YAML failed to parse
      to a mapping (treat as parse error).
    * ``(raw, dict)`` — frontmatter parsed.
    """
    if not content_md:
        return None, {}
    match = _FRONT_MATTER_RE.match(content_md)
    if match is None:
        return None, {}
    raw = match.group("body")
    try:
        parsed = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return raw, {}
    if not isinstance(parsed, dict):
        return raw, {}
    return raw, dict(parsed)


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _is_nonempty_list(value: object) -> bool:
    return isinstance(value, list) and len(value) > 0


def _collect_warnings(parsed: dict[str, object] | None) -> tuple[
    list[PlanMarkerWarning], tuple[str, ...]
]:
    """Compute the warning list for a parsed (or None) frontmatter dict.

    Returns ``(warnings, fields_present)``.
    """
    if parsed is None:
        warnings: list[PlanMarkerWarning] = [
            PlanMarkerWarning(code="PLAN_MARKER_FRONT_MATTER_MISSING")
        ]
        warnings.extend(
            PlanMarkerWarning(code="PLAN_MARKER_MISSING_FIELD", field=name)
            for name in REQUIRED_PLAN_FIELDS
        )
        return warnings, ()

    if not parsed:
        warnings = [PlanMarkerWarning(code="PLAN_MARKER_PARSE_ERROR")]
        warnings.extend(
            PlanMarkerWarning(code="PLAN_MARKER_MISSING_FIELD", field=name)
            for name in REQUIRED_PLAN_FIELDS
        )
        return warnings, ()

    warnings = []
    fields_present: list[str] = []
    for name in REQUIRED_PLAN_FIELDS:
        if name not in parsed or parsed[name] is None:
            warnings.append(
                PlanMarkerWarning(code="PLAN_MARKER_MISSING_FIELD", field=name)
            )
            continue
        value = parsed[name]
        if name == "title":
            if _is_nonempty_string(value):
                fields_present.append(name)
            else:
                warnings.append(
                    PlanMarkerWarning(code="PLAN_MARKER_MISSING_FIELD", field=name)
                )
        elif name == "dependencies":
            # v0.12 M55C (E6): explicit empty list means "no dependencies".
            # A list of any length is present; only a missing/None/blank/
            # non-list-non-string value warns. Kills the "- none" sentinel.
            if _is_nonempty_list(value) or _is_nonempty_string(value) or (
                isinstance(value, list) and not value
            ):
                fields_present.append(name)
            else:
                warnings.append(
                    PlanMarkerWarning(
                        code="PLAN_MARKER_EMPTY_LIST", field=name
                    )
                )
        else:
            if _is_nonempty_list(value) or _is_nonempty_string(value):
                fields_present.append(name)
            else:
                warnings.append(
                    PlanMarkerWarning(
                        code="PLAN_MARKER_EMPTY_LIST", field=name
                    )
                )
    return warnings, tuple(fields_present)


def validate_plan_frontmatter(
    content_md: str | None,
) -> PlanMarkerValidationResult:
    """Soft validator — never raises. Returns warnings and the fields
    that were detected as present.
    """
    raw, parsed = _extract_frontmatter(content_md)
    if raw is None:
        warnings, fields_present = _collect_warnings(None)
        return PlanMarkerValidationResult(
            warnings=warnings, fields_present=fields_present, frontmatter={}
        )
    if not parsed:
        warnings, fields_present = _collect_warnings({})
        return PlanMarkerValidationResult(
            warnings=warnings, fields_present=fields_present, frontmatter={}
        )
    warnings, fields_present = _collect_warnings(parsed)
    return PlanMarkerValidationResult(
        warnings=warnings,
        fields_present=fields_present,
        frontmatter=parsed,
    )


def assert_plan_frontmatter_ok(content_md: str | None) -> PlanMarkerValidationResult:
    """Hard validator — raises ``StateTransitionError`` with
    ``error_code=STATE_MACHINE_PLAN_MARKER_MISSING`` when required
    fields are missing or malformed. Returns the (always valid) result
    on success.

    Wired into ``plan_revise`` and ``create_experiment`` per plan (a)
    acceptance so a plan without frontmatter or required fields is
    refused with HTTP 422 + structured error envelope.
    """
    raw, parsed = _extract_frontmatter(content_md)
    if raw is None:
        raise StateTransitionError(
            "Plan frontmatter is missing",
            error_code="STATE_MACHINE_PLAN_MARKER_MISSING",
            hint=_frontmatter_template_hint(list(REQUIRED_PLAN_FIELDS)),
        )
    if not parsed:
        raise StateTransitionError(
            "Plan frontmatter could not be parsed as a YAML mapping",
            error_code="STATE_MACHINE_PLAN_MARKER_MISSING",
            hint=_frontmatter_template_hint(),
        )

    warnings, fields_present = _collect_warnings(parsed)
    if warnings:
        missing = sorted(
            w.field
            for w in warnings
            if w.code in ("PLAN_MARKER_MISSING_FIELD", "PLAN_MARKER_EMPTY_LIST")
            and w.field
        )
        detail = ", ".join(missing) if missing else "see warnings"
        raise StateTransitionError(
            f"Plan frontmatter is missing required fields: {detail}",
            error_code="STATE_MACHINE_PLAN_MARKER_MISSING",
            hint=_frontmatter_template_hint(missing),
        )

    return PlanMarkerValidationResult(
        warnings=[],
        fields_present=fields_present,
        frontmatter=parsed,
    )
