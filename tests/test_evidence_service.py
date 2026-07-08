"""8ac93d4e I1.b — server ``validate_log_evidence`` unit tests.

Pure-function tests against the service-layer soft validation. The
service is intentionally import-safe without a DB session.
"""

from __future__ import annotations

from server.services.evidence_service import (
    EvidenceValidationResult,
    EvidenceWarning,
    validate_log_evidence,
)

_PLAN_FULL = (
    "---\n"
    "evidence_keys:\n"
    "  - pytest_summary\n"
    "  - alembic_current\n"
    "  - api_health\n"
    "---\n"
    "# body\n"
)


def test_validate_no_plan_returns_empty():
    result = validate_log_evidence(plan_md=None, metadata={"pytest_summary": "ok"})
    assert result == EvidenceValidationResult()


def test_validate_empty_plan_returns_empty():
    result = validate_log_evidence(plan_md="", metadata={"pytest_summary": "ok"})
    assert result == EvidenceValidationResult()


def test_validate_plan_without_frontmatter_returns_empty():
    plan_md = "# Experiment plan\n\n## acceptance\n- (a) ...\n"
    result = validate_log_evidence(plan_md=plan_md, metadata={"pytest_summary": "ok"})
    assert result.warnings == []
    assert result.parse_error is None


def test_validate_all_declared_no_warnings():
    metadata = {
        "pytest_summary": "12 passed",
        "alembic_current": "035 (head)",
        "api_health": "200",
    }
    result = validate_log_evidence(plan_md=_PLAN_FULL, metadata=metadata)
    assert result.warnings == []
    assert result.parse_error is None
    assert result.plan_keys == ("pytest_summary", "alembic_current", "api_health")


def test_validate_partial_declared_emits_warnings():
    metadata = {"pytest_summary": "12 passed"}  # missing 2/3
    result = validate_log_evidence(plan_md=_PLAN_FULL, metadata=metadata)
    assert len(result.warnings) == 2
    missing = {w.missing_key for w in result.warnings}
    assert missing == {"alembic_current", "api_health"}
    for w in result.warnings:
        assert w.code == "MISSING_EVIDENCE_KEY"
        assert w.plan_required is True
        assert w.log_provided is False


def test_validate_metadata_none_with_plan_keys_emits_all_warnings():
    result = validate_log_evidence(plan_md=_PLAN_FULL, metadata=None)
    assert len(result.warnings) == 3
    assert {w.missing_key for w in result.warnings} == {
        "pytest_summary",
        "alembic_current",
        "api_health",
    }


def test_validate_metadata_empty_dict_with_plan_keys_emits_all_warnings():
    result = validate_log_evidence(plan_md=_PLAN_FULL, metadata={})
    assert len(result.warnings) == 3


def test_validate_plan_frontmatter_parse_error_records_parse_error():
    plan_md = (
        "---\n"
        "evidence_keys:\n"
        "  - pytest_summary\n"
        "  malformed: : :\n"
        "---\n"
    )
    result = validate_log_evidence(plan_md=plan_md, metadata={"pytest_summary": "ok"})
    assert result.warnings == []
    assert result.parse_error is not None
    assert "YAML parse failed" in result.parse_error


def test_validate_plan_frontmatter_without_evidence_keys_key():
    plan_md = "---\ntitle: foo\n---\n"
    result = validate_log_evidence(plan_md=plan_md, metadata={"pytest_summary": "ok"})
    assert result.warnings == []
    assert result.parse_error is None


def test_validate_result_valid_is_always_true():
    """Soft validation never blocks log save."""
    cases = [
        EvidenceValidationResult(),
        EvidenceValidationResult(
            warnings=[EvidenceWarning(code="MISSING_EVIDENCE_KEY", missing_key="x")]
        ),
        EvidenceValidationResult(parse_error="boom"),
    ]
    for r in cases:
        assert r.valid is True


def test_validate_extra_metadata_fields_dont_emit_warnings():
    """Unrelated metadata keys are ignored — only missing plan_keys matter."""
    metadata = {
        "pytest_summary": "ok",
        "alembic_current": "035",
        "api_health": "200",
        "extra_field": "ignored",
        "another_extra": 42,
    }
    result = validate_log_evidence(plan_md=_PLAN_FULL, metadata=metadata)
    assert result.warnings == []


def test_validate_warnings_dataclass_field_shape():
    metadata = {"pytest_summary": "ok"}
    result = validate_log_evidence(plan_md=_PLAN_FULL, metadata=metadata)
    assert len(result.warnings) == 2
    for w in result.warnings:
        assert isinstance(w, EvidenceWarning)
        assert hasattr(w, "code")
        assert hasattr(w, "missing_key")
        assert hasattr(w, "plan_required")
        assert hasattr(w, "log_provided")
