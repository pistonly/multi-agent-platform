"""b72d0542 I1.a — Result submission 4-段 template soft validator.

Wraps :mod:`map_client.result_template` with structured warnings and
the soft-validation invariant (``valid`` is always True so the
``experiment complete`` call is never blocked).

Used by the complete path (planned I1.b: wire into ``complete_experiment``
endpoint and surface via CLI). Soft warnings:

* ``MISSING_TEMPLATE_SECTION`` — one of the 4 required H2 sections is
  absent from the result submission content.
* ``NO_LINK_IN_LOG_SECTION`` — ``## 实施 log`` section is present but
  contains no ``[text](url)`` markdown link.
* ``MALFORMED_MARKDOWN_LINK`` — ``## 实施 log`` section has a
  link-like fragment that is unbalanced (``[text`` without ``]`` or
  ``(url`` without ``)``).

The warnings are returned as a list of frozen dataclasses; the API/SDK
wrappers (planned I1.b) convert these into Pydantic schemas with the
same shape as the evidence warnings (8ac93d4e).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from map_client.result_template import (
    REQUIRED_SECTION_NAMES,
    extract_malformed_link_fragment,
    parse_result_submission,
)

TemplateWarningCode = Literal[
    "MISSING_TEMPLATE_SECTION",
    "NO_LINK_IN_LOG_SECTION",
    "MALFORMED_MARKDOWN_LINK",
]


@dataclass(frozen=True)
class TemplateWarning:
    code: TemplateWarningCode
    section: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class TemplateValidationResult:
    warnings: list[TemplateWarning] = field(default_factory=list)
    sections_present: tuple[str, ...] = ()
    log_link_count: int = 0

    @property
    def valid(self) -> bool:
        """Soft validation invariant: always True.

        Mirrors :class:`evidence_service.EvidenceValidationResult.valid`.
        Host is expected to follow up with another log if warnings
        indicate missing sections; the validator never blocks the save.
        """
        return True


def validate_result_submission_template(
    content_md: str | None,
) -> TemplateValidationResult:
    """Validate that ``content_md`` follows the 4-段 template contract.

    Soft validation — returns warnings but never raises. The complete
    path always succeeds; the validator is purely advisory.

    Acceptance cases (from plan acceptance (a)):

    1. **完整 4 段**: all 4 sections present, ``## 实施 log`` has at
       least one well-formed ``[text](url)`` → ``warnings=[]``.
    2. **缺 summary**: any of the 4 sections missing →
       ``MISSING_TEMPLATE_SECTION`` per missing section.
    3. **缺 acceptance 清单**: same as case 2 — coverage by
       ``MISSING_TEMPLATE_SECTION``.
    4. **markdown link 格式错误**: ``## 实施 log`` present but contains
       an unbalanced ``[text`` or ``(url`` fragment →
       ``MALFORMED_MARKDOWN_LINK`` with the offending fragment in
       ``detail``. A section with no link at all produces
       ``NO_LINK_IN_LOG_SECTION`` instead (different code).
    """
    if not content_md:
        # Empty body → all 4 sections missing.
        warnings = [
            TemplateWarning(code="MISSING_TEMPLATE_SECTION", section=name)
            for name in REQUIRED_SECTION_NAMES
        ]
        return TemplateValidationResult(warnings=warnings, sections_present=())

    parsed = parse_result_submission(content_md)

    # Sections present in canonical order.
    present_in_order = tuple(
        name for name in REQUIRED_SECTION_NAMES if name in parsed.present
    )

    warnings = []

    # 1. Missing-section warnings.
    for name in REQUIRED_SECTION_NAMES:
        if not parsed.present[name]:
            warnings.append(TemplateWarning(code="MISSING_TEMPLATE_SECTION", section=name))

    # 2 & 3. Log-section link warnings (only meaningful when log section exists).
    if parsed.log_present:
        if not parsed.log_links:
            warnings.append(
                TemplateWarning(code="NO_LINK_IN_LOG_SECTION", section="实施 log")
            )
        else:
            # Detect malformed fragments inside the log section body.
            from map_client.result_template import _slice_section_body

            log_body = _slice_section_body(content_md, "实施 log") or ""
            fragment = extract_malformed_link_fragment(log_body)
            if fragment is not None:
                warnings.append(
                    TemplateWarning(
                        code="MALFORMED_MARKDOWN_LINK",
                        section="实施 log",
                        detail=fragment,
                    )
                )

    return TemplateValidationResult(
        warnings=warnings,
        sections_present=present_in_order,
        log_link_count=len(parsed.log_links),
    )
