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
