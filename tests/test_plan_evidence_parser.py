"""8ac93d4e I1.a — SDK ``parse_plan_evidence_keys`` unit tests.

Pins the parser contract documented in
``docs/MAP-EVIDENCE-METADATA.md``. These tests run without any DB /
server — they cover the pure-function parser that the server-side
``validate_log_evidence`` builds on.
"""

from __future__ import annotations

import pytest
from map_client.plan_evidence import (
    PlanEvidenceKeys,
    PlanFrontmatterParseError,
    parse_plan_evidence_keys,
)

# ---- happy paths ----------------------------------------------------------


def test_parse_no_frontmatter_returns_empty_keys():
    plan_md = "# Experiment plan\n\n## acceptance\n- (a) ...\n"
    result = parse_plan_evidence_keys(plan_md)
    assert result == PlanEvidenceKeys(keys=(), has_frontmatter=False)
    assert bool(result) is False


def test_parse_frontmatter_without_evidence_keys_key():
    plan_md = (
        "---\n"
        "title: foo\n"
        "owner: host\n"
        "---\n"
        "# body\n"
    )
    result = parse_plan_evidence_keys(plan_md)
    assert result == PlanEvidenceKeys(keys=(), has_frontmatter=True)


def test_parse_frontmatter_with_evidence_keys():
    plan_md = (
        "---\n"
        "evidence_keys:\n"
        "  - pytest_summary\n"
        "  - alembic_current\n"
        "  - api_health\n"
        "---\n"
        "# body\n"
    )
    result = parse_plan_evidence_keys(plan_md)
    assert result.keys == ("pytest_summary", "alembic_current", "api_health")
    assert result.has_frontmatter is True
    assert result.parse_error is None
    assert bool(result) is True


def test_parse_frontmatter_trims_whitespace_and_dedupes():
    plan_md = (
        "---\n"
        "evidence_keys:\n"
        "  - pytest_summary\n"
        "  - '  alembic_current  '\n"
        "  - pytest_summary\n"
        "---\n"
    )
    result = parse_plan_evidence_keys(plan_md)
    assert result.keys == ("pytest_summary", "alembic_current")


def test_parse_frontmatter_terminates_at_end_of_string():
    """Plan frontmatter may end without a trailing newline."""
    plan_md = (
        "---\n"
        "evidence_keys:\n"
        "  - pytest_summary\n"
        "---"
    )
    result = parse_plan_evidence_keys(plan_md)
    assert result.keys == ("pytest_summary",)


def test_parse_empty_frontmatter_body():
    plan_md = "---\n---\n# body\n"
    result = parse_plan_evidence_keys(plan_md)
    assert result == PlanEvidenceKeys(keys=(), has_frontmatter=True)


def test_parse_frontmatter_followed_by_more_content():
    plan_md = (
        "---\n"
        "evidence_keys:\n"
        "  - pytest_summary\n"
        "---\n"
        "## acceptance\n- (a) ...\n"
    )
    result = parse_plan_evidence_keys(plan_md)
    assert result.keys == ("pytest_summary",)


# ---- error paths ----------------------------------------------------------


def test_parse_frontmatter_yaml_syntax_error_raises():
    plan_md = (
        "---\n"
        "evidence_keys:\n"
        "  - pytest_summary\n"
        "  malformed: : :\n"
        "---\n"
    )
    with pytest.raises(PlanFrontmatterParseError) as exc_info:
        parse_plan_evidence_keys(plan_md)
    assert exc_info.value.frontmatter is not None
    assert "YAML parse failed" in str(exc_info.value)


def test_parse_evidence_keys_not_a_list_raises():
    plan_md = (
        "---\n"
        "evidence_keys: pytest_summary\n"
        "---\n"
    )
    with pytest.raises(PlanFrontmatterParseError) as exc_info:
        parse_plan_evidence_keys(plan_md)
    assert "must be a YAML list" in str(exc_info.value)


def test_parse_evidence_keys_contains_non_string_raises():
    plan_md = (
        "---\n"
        "evidence_keys:\n"
        "  - pytest_summary\n"
        "  - 123\n"
        "---\n"
    )
    with pytest.raises(PlanFrontmatterParseError) as exc_info:
        parse_plan_evidence_keys(plan_md)
    assert "must be a string" in str(exc_info.value)


def test_parse_evidence_keys_empty_string_raises():
    plan_md = (
        "---\n"
        "evidence_keys:\n"
        "  - '   '\n"
        "---\n"
    )
    with pytest.raises(PlanFrontmatterParseError) as exc_info:
        parse_plan_evidence_keys(plan_md)
    assert "non-empty" in str(exc_info.value)


def test_parse_frontmatter_top_level_not_mapping_raises():
    plan_md = (
        "---\n"
        "- pytest_summary\n"
        "---\n"
    )
    with pytest.raises(PlanFrontmatterParseError) as exc_info:
        parse_plan_evidence_keys(plan_md)
    assert "must be a YAML mapping" in str(exc_info.value)


def test_parse_non_frontmatter_starting_with_dashes():
    """A doc starting with ``---foo`` (no newline) is NOT a frontmatter."""
    plan_md = "--- not frontmatter\n# body\n"
    result = parse_plan_evidence_keys(plan_md)
    assert result.has_frontmatter is False
