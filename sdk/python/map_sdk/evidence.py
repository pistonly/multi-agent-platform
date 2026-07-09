"""map_sdk.evidence — completion evidence detection.

arch experiment (0519e2a3) PR2: move ``metadata_has_completion_evidence``
+ its constants from ``server/services/evidence_service`` here so the
CLI can ``from map_sdk.evidence import ...`` without dragging the
server package on its import path.

Pure stdlib + dataclass — no SQLAlchemy / FastAPI / server imports.
CI grep gate asserts ``grep -r "from server" map_sdk/`` is empty.

Note: ``validate_log_evidence`` / ``EvidenceValidationResult`` /
``EvidenceWarning`` stay in ``server.services.evidence_service`` for
now — they depend on ``map_client.plan_evidence`` and are only used
server-side. Moving them is a follow-up PR.
"""

from __future__ import annotations


EVIDENCE_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "alembic_revision",
        "alembic_current",
        "api_health",
        "health",
        "smoke",
        "smoke_result",
        "pytest_summary",
        "test_summary",
        "image_digest",
        "acceptance",
    }
)


def metadata_has_completion_evidence(metadata: object) -> bool:
    """Return True if ``metadata`` carries any completion evidence.

    The original lives in ``server.services.evidence_service`` and was
    duplicated as-is in the CLI (``cli/main.py``) and the server
    (``phase_service``, ``acceptance_service``). The semantics:

    * non-dict → False
    * ``allow_missing_evidence`` True → True (operator override)
    * any EVIDENCE_METADATA_KEYS with non-empty value → True
    * metadata["evidence"] dict with at least one non-empty value → True
    * metadata["evidence"] list with at least one entry → True
    """
    if not isinstance(metadata, dict):
        return False
    if metadata.get("allow_missing_evidence") is True:
        return True
    if any(
        key in metadata and metadata[key] not in (None, "", [], {})
        for key in EVIDENCE_METADATA_KEYS
    ):
        return True
    evidence = metadata.get("evidence")
    if isinstance(evidence, dict):
        return any(value not in (None, "", [], {}) for value in evidence.values())
    if isinstance(evidence, list):
        return bool(evidence)
    return False


__all__ = [
    "EVIDENCE_METADATA_KEYS",
    "metadata_has_completion_evidence",
]
