"""b72d0542 I1.a — Result submission 4-段 template parser.

Extracts 4 required sections from a result submission markdown body so the
service layer can soft-warn on missing or malformed sections. Soft
validation only — host is expected to follow up; the validator never
blocks ``experiment complete``.

The 4 required sections (markdown ``##`` H2 headings; case-insensitive
substring match against the section name):

* ``## summary`` — one-line result summary
* ``## 实施 log`` — running logs, must contain at least one markdown link
* ``## 风险`` — risks section
* ``## acceptance`` — acceptance criteria checklist

Behavior contract (pinned by ``docs/MAP-RESULT-SUBMISSION-TEMPLATE.md``):

* No content (empty/None) → empty result (nothing to validate).
* All 4 sections present + each with valid markdown link (where required)
  → empty warnings list.
* Missing any of the 4 sections → ``MISSING_TEMPLATE_SECTION`` warning.
* ``## 实施 log`` section present but no ``[text](url)`` markdown link
  → ``NO_LINK_IN_LOG_SECTION`` warning.
* ``## 实施 log`` section has a malformed link (e.g. ``[text`` without
  closing ``]`` or ``(`` without ``)``) → ``MALFORMED_MARKDOWN_LINK``
  warning with the offending link text.

Parsing helpers live here; service-level validation (warn codes,
dataclass wrapper, valid=True invariant) lives in
``server/services/template_service.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 4 required section names (substring, lowercase compare).
REQUIRED_SECTION_NAMES: tuple[str, ...] = (
    "summary",
    "实施 log",
    "风险",
    "acceptance",
)

# Match a ``## <heading>`` line. Section names may include spaces / CJK.
_SECTION_HEADING_RE = re.compile(r"^##\s+(?P<name>.+?)\s*$", re.MULTILINE)

# Match ``[text](url)`` markdown link. Greedy on text/url up to 1/2 parens
# balanced; this is best-effort (markdown spec is more permissive than
# this regex). Captures: 1=text, 2=url.
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

# Match an unbalanced markdown-link-like fragment: ``[text`` without ``]``
# or ``(url`` without ``)``. Used to surface malformed links as warnings.
_UNBALANCED_OPEN_BRACKET_RE = re.compile(r"\[[^\]\n]*$", re.MULTILINE)
_UNBALANCED_OPEN_PAREN_RE = re.compile(r"\([^)\n]*$", re.MULTILINE)


@dataclass(frozen=True)
class ResultTemplateSections:
    """Parsed sections of a result submission markdown body.

    Each ``<section>_present`` is True iff a heading matching the
    canonical name appears anywhere in the body. ``log_links`` is the
    list of (text, url) tuples extracted from inside the ``## 实施 log``
    section body (empty if section missing).
    """

    summary_present: bool
    log_present: bool
    risk_present: bool
    acceptance_present: bool
    log_links: tuple[tuple[str, str], ...] = ()

    @property
    def present(self) -> dict[str, bool]:
        return {
            "summary": self.summary_present,
            "实施 log": self.log_present,
            "风险": self.risk_present,
            "acceptance": self.acceptance_present,
        }


def _extract_section_names(content: str) -> list[str]:
    """Return the section names (text after ``##``) in document order."""
    return [m.group("name").strip() for m in _SECTION_HEADING_RE.finditer(content)]


def _slice_section_body(content: str, section_name: str) -> str | None:
    """Return the body of the named ``##`` section (text after the heading
    line until the next ``##`` heading or end of document), or None if
    the section is not found.
    """
    headings = list(_SECTION_HEADING_RE.finditer(content))
    target_idx: int | None = None
    target_match_pos: int = -1
    for idx, m in enumerate(headings):
        if m.group("name").strip().lower() == section_name.lower():
            target_idx = idx
            target_match_pos = m.end()
            break
    if target_idx is None:
        return None
    if target_idx + 1 < len(headings):
        return content[target_match_pos : headings[target_idx + 1].start()]
    return content[target_match_pos:]


def parse_result_submission(content: str | None) -> ResultTemplateSections:
    """Parse a result submission markdown body.

    Returns a :class:`ResultTemplateSections` summarising which of the 4
    required sections are present and the markdown links found inside
    the ``## 实施 log`` section.

    Section detection is case-insensitive substring match (canonical
    names compared via ``str.lower()``). The 4 canonical names are:

    * ``summary``
    * ``实施 log``
    * ``风险``
    * ``acceptance``
    """
    if not content:
        return ResultTemplateSections(False, False, False, False, ())

    sections = _extract_section_names(content)
    section_set_lower = {s.lower() for s in sections}

    summary_present = "summary" in section_set_lower
    log_present = "实施 log" in section_set_lower
    risk_present = "风险" in section_set_lower
    acceptance_present = "acceptance" in section_set_lower

    log_links: tuple[tuple[str, str], ...] = ()
    if log_present:
        log_body = _slice_section_body(content, "实施 log") or ""
        log_links = tuple(
            (m.group(1).strip(), m.group(2).strip())
            for m in _MARKDOWN_LINK_RE.finditer(log_body)
        )

    return ResultTemplateSections(
        summary_present=summary_present,
        log_present=log_present,
        risk_present=risk_present,
        acceptance_present=acceptance_present,
        log_links=log_links,
    )


def extract_malformed_link_fragment(log_body: str) -> str | None:
    """Return the offending unbalanced fragment in ``## 实施 log`` body, if any.

    Surfaces a hint for ``MALFORMED_MARKDOWN_LINK`` warnings. Returns
    None when no unbalanced ``[...`` or ``(...`` tail is found (i.e. all
    link-like fragments are well-formed or absent).
    """
    open_bracket = _UNBALANCED_OPEN_BRACKET_RE.search(log_body)
    open_paren = _UNBALANCED_OPEN_PAREN_RE.search(log_body)
    if open_bracket:
        return open_bracket.group(0).strip()
    if open_paren:
        return open_paren.group(0).strip()
    return None
