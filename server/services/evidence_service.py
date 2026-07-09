from dataclasses import dataclass, field
from typing import Literal

from map_client.plan_evidence import (
    PlanEvidenceKeys,
    PlanFrontmatterParseError,
    parse_plan_evidence_keys,
)

# arch experiment (0519e2a3) PR2: ``EVIDENCE_METADATA_KEYS`` and
# ``metadata_has_completion_evidence`` moved to ``map_sdk.evidence``.
# Keep them re-exported from here so existing server-side imports
# keep working without churning every call site in this PR. CLI
# switched to ``from map_sdk.evidence import ...`` already.
from map_sdk.evidence import (  # noqa: E402, F401
    EVIDENCE_METADATA_KEYS,
    metadata_has_completion_evidence,
)


# --- 8ac93d4e I1.b — plan evidence_keys soft validation ---------------------


WarningCode = Literal["MISSING_EVIDENCE_KEY"]


@dataclass(frozen=True)
class EvidenceWarning:
    code: WarningCode
    missing_key: str
    plan_required: bool = True
    log_provided: bool = False


@dataclass(frozen=True)
class EvidenceValidationResult:
    warnings: list[EvidenceWarning] = field(default_factory=list)
    parse_error: str | None = None
    plan_keys: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        """Soft validation: always True. Host is expected to follow up
        with another log if warnings indicate missing evidence."""
        return True


def validate_log_evidence(
    *,
    plan_md: str | None,
    metadata: dict | None,
) -> EvidenceValidationResult:
    """Compare ``metadata`` keys against plan ``evidence_keys``.

    Cases:
    * ``plan_md`` is None or empty → empty result (no plan to validate against).
    * Plan has frontmatter but YAML parse fails → ``parse_error`` set,
      no warnings (caller surfaces stderr warn).
    * Plan frontmatter OK + has ``evidence_keys`` → return
      ``MISSING_EVIDENCE_KEY`` warning for each plan key absent from
      ``metadata``.
    * Plan frontmatter OK but no ``evidence_keys`` key → empty result.
    """
    if not plan_md:
        return EvidenceValidationResult()

    try:
        parsed: PlanEvidenceKeys = parse_plan_evidence_keys(plan_md)
    except PlanFrontmatterParseError as exc:
        return EvidenceValidationResult(parse_error=str(exc))

    if not parsed.keys:
        return EvidenceValidationResult(plan_keys=parsed.keys)

    provided = set(metadata.keys()) if isinstance(metadata, dict) else set()
    warnings: list[EvidenceWarning] = []
    for key in parsed.keys:
        if key not in provided:
            warnings.append(
                EvidenceWarning(
                    code="MISSING_EVIDENCE_KEY",
                    missing_key=key,
                    plan_required=True,
                    log_provided=False,
                )
            )
    return EvidenceValidationResult(warnings=warnings, plan_keys=parsed.keys)


