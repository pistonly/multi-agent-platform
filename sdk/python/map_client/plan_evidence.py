"""8ac93d4e I1.a — Plan evidence_keys frontmatter parser.

Extracts ``evidence_keys`` from YAML frontmatter in plan markdown so the
CLI / SDK / server can validate that ``ExperimentLogCreate.metadata``
covers every key the plan declared. Soft validation only — a missing
key is a warning, never a block.

Frontmatter format::

    ---
    evidence_keys:
      - pytest_summary
      - alembic_current
      - api_health
    ---

Behavior contract (pinned by ``docs/MAP-EVIDENCE-METADATA.md``):

* No frontmatter → ``PlanEvidenceKeys(keys=(), has_frontmatter=False)``.
* Frontmatter without ``evidence_keys`` key → ``keys=(), has_frontmatter=True``.
* Frontmatter with malformed YAML → raises :class:`PlanFrontmatterParseError`.
* ``evidence_keys`` must be a list of non-empty strings; other shapes
  (dict, scalar, list of non-strings) raise ``PlanFrontmatterParseError``.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml


class PlanFrontmatterParseError(Exception):
    """Raised when plan frontmatter YAML cannot be parsed.

    Carries the raw frontmatter block + underlying YAML error so the
    caller can render a friendly stderr warning without losing the
    diagnostic detail.
    """

    def __init__(self, message: str, *, frontmatter: str | None = None) -> None:
        super().__init__(message)
        self.frontmatter = frontmatter


@dataclass(frozen=True)
class PlanEvidenceKeys:
    """Result of parsing ``evidence_keys`` from plan markdown frontmatter."""

    keys: tuple[str, ...]
    has_frontmatter: bool
    parse_error: str | None = None

    def __bool__(self) -> bool:
        return bool(self.keys)


_FRONTMATTER_OPEN = "---"
_LIST_TYPES = (list, tuple)


def _split_frontmatter(plan_md: str) -> tuple[str | None, str]:
    """Return ``(frontmatter_body_or_none, remainder)``.

    Frontmatter is detected by the document starting with ``---\\n`` and
    terminated by ``\\n---\\n`` / ``\\n---`` / ``---\\n`` at the start of
    body / ``---`` at end-of-string. Returns ``(None, plan_md)`` when no
    frontmatter is present.
    """
    if not plan_md.startswith(_FRONTMATTER_OPEN + "\n"):
        return None, plan_md
    body = plan_md[len(_FRONTMATTER_OPEN) + 1 :]
    # Closing fence variants (in priority order):
    # 1. ``\\n---\\n`` somewhere after the opening fence.
    end_with_newline = body.find("\n" + _FRONTMATTER_OPEN + "\n", 3)
    if end_with_newline != -1:
        return (
            body[:end_with_newline],
            body[end_with_newline + len(_FRONTMATTER_OPEN) + 2 :],
        )
    # 2. ``---\\n`` at the very start of body (empty frontmatter case).
    if body.startswith(_FRONTMATTER_OPEN + "\n"):
        return "", body[len(_FRONTMATTER_OPEN) + 1 :]
    # 3. ``---\\n`` at end-of-string with trailing newline.
    if body.endswith(_FRONTMATTER_OPEN + "\n"):
        return body[: -len(_FRONTMATTER_OPEN) - 1], ""
    # 4. ``---`` at end-of-string without trailing newline.
    if body.endswith(_FRONTMATTER_OPEN) and len(body) > len(_FRONTMATTER_OPEN):
        return body[: -len(_FRONTMATTER_OPEN)], ""
    return None, plan_md


def _validate_evidence_keys_value(value: object) -> tuple[str, ...]:
    """Coerce parsed YAML ``evidence_keys`` value into a tuple of strings.

    Raises :class:`PlanFrontmatterParseError` for non-list shapes or
    non-string / empty-string entries.
    """
    if not isinstance(value, _LIST_TYPES):
        raise PlanFrontmatterParseError(
            "evidence_keys must be a YAML list, got "
            f"{type(value).__name__}"
        )
    keys: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str):
            raise PlanFrontmatterParseError(
                f"evidence_keys[{idx}] must be a string, got "
                f"{type(item).__name__}"
            )
        stripped = item.strip()
        if not stripped:
            raise PlanFrontmatterParseError(
                f"evidence_keys[{idx}] must be non-empty"
            )
        keys.append(stripped)
    # Dedupe while preserving order (first occurrence wins).
    seen: set[str] = set()
    deduped: list[str] = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            deduped.append(key)
    return tuple(deduped)


def parse_plan_evidence_keys(plan_md: str) -> PlanEvidenceKeys:
    """Extract ``evidence_keys`` from YAML frontmatter in plan markdown.

    See module docstring for the full behavior contract.
    """
    frontmatter, _ = _split_frontmatter(plan_md)
    if frontmatter is None:
        return PlanEvidenceKeys(keys=(), has_frontmatter=False)

    try:
        parsed = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        raise PlanFrontmatterParseError(
            f"frontmatter YAML parse failed: {exc}",
            frontmatter=frontmatter,
        ) from exc

    if parsed is None:
        # Frontmatter present but empty.
        return PlanEvidenceKeys(keys=(), has_frontmatter=True)

    if not isinstance(parsed, dict):
        raise PlanFrontmatterParseError(
            "frontmatter top-level must be a YAML mapping, got "
            f"{type(parsed).__name__}",
            frontmatter=frontmatter,
        )

    if "evidence_keys" not in parsed:
        return PlanEvidenceKeys(keys=(), has_frontmatter=True)

    keys = _validate_evidence_keys_value(parsed["evidence_keys"])
    return PlanEvidenceKeys(keys=keys, has_frontmatter=True)
