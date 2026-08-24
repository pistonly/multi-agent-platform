"""8ac93d4e I1.b — server ``validate_log_evidence`` unit tests.

Pure-function tests against the service-layer soft validation. The
service is intentionally import-safe without a DB session.
"""

from __future__ import annotations

from server.services.evidence_service import (
    EvidenceValidationResult,
    EvidenceWarning,
    PytestSummaryValidation,
    validate_log_evidence,
    validate_pytest_summary,
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


# --- 50cddb7e I4 (A3) — complete 时 pytest_summary 机器校验 ---------------------


_GREEN = {"total": 35, "passed": 35, "failed": 0}
_RED = {"total": 35, "passed": 30, "failed": 5}


def test_pytest_summary_ok_no_known_failures_passes():
    result = validate_pytest_summary(_GREEN)
    assert result == PytestSummaryValidation()
    assert result.reject_reason is None


def test_pytest_summary_failed_gt_zero_rejects_without_exemption():
    result = validate_pytest_summary(_RED)
    assert result.reject_reason is not None
    assert "failed=5 > 0" in result.reject_reason
    assert "complete 被拒" in result.reject_reason
    assert "--known-failures" in result.reject_reason
    assert result.exempted is False


def test_pytest_summary_failed_gt_zero_exempted_with_known_failures():
    result = validate_pytest_summary(_RED, known_failures=["#42", "test_zz_slow"])
    assert result.reject_reason is None
    assert result.exempted is True
    assert result.exempted_known_failures == ("#42", "test_zz_slow")
    assert any("豁免" in w for w in result.warnings)


def test_pytest_summary_missing_failed_key_is_not_rejected():
    """total-only dict has no failed signal — no hard gate, no warning."""
    result = validate_pytest_summary({"total": 35})
    assert result.reject_reason is None
    assert result.warnings == ()


def test_pytest_summary_non_dict_is_soft_warning_only():
    """Legacy string form (e.g. test_m3_flow "unit passed") → warning, never reject."""
    result = validate_pytest_summary("unit passed")
    assert result.reject_reason is None
    assert result.exempted is False
    assert len(result.warnings) == 1
    assert "结构化形态" in result.warnings[0]


def test_pytest_summary_total_mismatch_against_ci_baseline_warns():
    result = validate_pytest_summary(
        {"total": 30, "passed": 30, "failed": 0},
        ci_baseline_total=35,
    )
    assert result.reject_reason is None
    assert any("total=30 与 CI 基线 35" in w for w in result.warnings)


def test_pytest_summary_total_match_against_ci_baseline_no_warnings():
    result = validate_pytest_summary(_GREEN, ci_baseline_total=35)
    assert result.reject_reason is None
    assert result.warnings == ()


def test_pytest_summary_failed_float_int_like_accepted():
    result = validate_pytest_summary({"total": 35, "passed": 30.0, "failed": 5.0})
    assert result.reject_reason is not None and "failed=5" in result.reject_reason


def test_pytest_summary_bool_values_are_treated_as_unparseable():
    """bool is int-like in Python but never a real test count."""
    result = validate_pytest_summary({"total": True, "passed": True, "failed": False})
    assert result.reject_reason is None
    assert result.warnings == ()
