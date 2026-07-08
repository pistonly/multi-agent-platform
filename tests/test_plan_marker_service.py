"""a764abf6 I1.(a) — plan frontmatter soft validator service unit tests.

Mirrors ``tests/test_template_service.py`` structure (5 cases pinned
from plan acceptance (a)):

1. complete frontmatter → no warnings
2. missing title → PLAN_MARKER_MISSING_FIELD(title)
3. missing evidence_keys → PLAN_MARKER_MISSING_FIELD(evidence_keys)
4. no frontmatter → PLAN_MARKER_FRONT_MATTER_MISSING + 4× MISSING_FIELD
5. soft-validation invariant: ``valid`` always True (even with errors)
"""

from __future__ import annotations

import pytest

from server.services.errors import StateTransitionError
from server.services.plan_marker_service import (
    REQUIRED_PLAN_FIELDS,
    PlanMarkerValidationResult,
    PlanMarkerWarning,
    assert_plan_frontmatter_ok,
    validate_plan_frontmatter,
)

_FULL_FRONTMATTER = """---
title: plan marker lint demo
acceptance:
  - 完整 frontmatter 不告警
  - 缺 title 报 PLAN_MARKER_MISSING_FIELD
evidence_keys:
  - pytest_summary
  - api_health
dependencies:
  - 0d5717d6-d672-46be-bc35-90ee08391f05
---

# 实验 plan: ...
"""


def test_case_1_complete_frontmatter_emits_no_warnings():
    result = validate_plan_frontmatter(_FULL_FRONTMATTER)
    assert result.warnings == []
    assert result.fields_present == REQUIRED_PLAN_FIELDS
    assert result.frontmatter["title"] == "plan marker lint demo"
    assert result.valid is True


def test_case_2_missing_title_emits_warning():
    body = """---
acceptance:
  - criteria
evidence_keys:
  - pytest_summary
dependencies:
  - 0d5717d6-d672-46be-bc35-90ee08391f05
---

# plan
"""
    result = validate_plan_frontmatter(body)
    missing = [w for w in result.warnings if w.code == "PLAN_MARKER_MISSING_FIELD"]
    assert len(missing) == 1
    assert missing[0].field == "title"
    assert "title" not in result.fields_present
    assert "acceptance" in result.fields_present
    assert result.valid is True


def test_case_3_missing_evidence_keys_emits_warning():
    body = """---
title: x
acceptance:
  - criteria
dependencies:
  - 0d5717d6-d672-46be-bc35-90ee08391f05
---

# plan
"""
    result = validate_plan_frontmatter(body)
    missing = [w for w in result.warnings if w.code == "PLAN_MARKER_MISSING_FIELD"]
    assert len(missing) == 1
    assert missing[0].field == "evidence_keys"
    assert result.valid is True


def test_case_4_no_frontmatter_emits_front_matter_missing_plus_4_field_warnings():
    body = "# 实验 plan without any frontmatter\n\nsome prose only"
    result = validate_plan_frontmatter(body)
    codes = [w.code for w in result.warnings]
    assert "PLAN_MARKER_FRONT_MATTER_MISSING" in codes
    missing_fields = [
        w.field
        for w in result.warnings
        if w.code == "PLAN_MARKER_MISSING_FIELD"
    ]
    assert sorted(missing_fields) == sorted(REQUIRED_PLAN_FIELDS)
    assert result.fields_present == ()
    assert result.valid is True


def test_soft_validation_invariant_always_true():
    """Even with the worst payload, ``valid`` stays True (soft warn)."""
    cases = [
        None,
        "",
        "no frontmatter",
        "---\n",  # empty frontmatter body
        "---\nnot a mapping: [1, 2\n",  # parse error
    ]
    for payload in cases:
        result = validate_plan_frontmatter(payload)
        assert isinstance(result, PlanMarkerValidationResult)
        assert result.valid is True
        assert isinstance(result.warnings, list)


def test_empty_list_field_emits_empty_list_warning():
    body = """---
title: x
acceptance: []
evidence_keys:
  - pytest_summary
dependencies:
  - 0d5717d6-d672-46be-bc35-90ee08391f05
---
"""
    result = validate_plan_frontmatter(body)
    empties = [w for w in result.warnings if w.code == "PLAN_MARKER_EMPTY_LIST"]
    assert len(empties) == 1
    assert empties[0].field == "acceptance"
    assert "title" in result.fields_present


def test_warning_dataclass_field_types():
    """Pin the dataclass shape so API schema mirrors it exactly."""
    body = "# no frontmatter"
    result = validate_plan_frontmatter(body)
    assert result.warnings
    for warning in result.warnings:
        assert isinstance(warning, PlanMarkerWarning)
        assert isinstance(warning.code, str)
        # code is one of the literal Literal values.
        assert warning.code.startswith("PLAN_MARKER_")


# --- hard validator (assert_plan_frontmatter_ok) ----------------------


def test_hard_validator_passes_on_complete_frontmatter():
    result = assert_plan_frontmatter_ok(_FULL_FRONTMATTER)
    assert result.fields_present == REQUIRED_PLAN_FIELDS
    assert result.warnings == []


@pytest.mark.parametrize(
    "missing_field",
    ["title", "acceptance", "evidence_keys", "dependencies"],
)
def test_hard_validator_raises_missing_field(missing_field):
    """3-bucket acceptance per plan (a): missing title / evidence_keys /
    dependencies / acceptance all raise STATE_MACHINE_PLAN_MARKER_MISSING.
    """
    fields = {
        "title": "x",
        "acceptance": ["criteria"],
        "evidence_keys": ["pytest_summary"],
        "dependencies": ["0d5717d6-d672-46be-bc35-90ee08391f05"],
    }
    fields.pop(missing_field)
    yaml_body = "\n".join(f"{k}: {v!r}" for k, v in fields.items())
    body = f"---\n{yaml_body}\n---\n# plan\n"
    with pytest.raises(StateTransitionError) as excinfo:
        assert_plan_frontmatter_ok(body)
    assert excinfo.value.error_code == "STATE_MACHINE_PLAN_MARKER_MISSING"
    assert excinfo.value.hint is not None
    assert missing_field in excinfo.value.hint


def test_hard_validator_raises_when_no_frontmatter():
    with pytest.raises(StateTransitionError) as excinfo:
        assert_plan_frontmatter_ok("# only a body, no frontmatter")
    assert excinfo.value.error_code == "STATE_MACHINE_PLAN_MARKER_MISSING"
    assert "frontmatter" in (excinfo.value.hint or "").lower()


def test_hard_validator_raises_when_empty_list_field():
    body = """---
title: x
acceptance: []
evidence_keys:
  - pytest_summary
dependencies:
  - 0d5717d6-d672-46be-bc35-90ee08391f05
---
"""
    with pytest.raises(StateTransitionError) as excinfo:
        assert_plan_frontmatter_ok(body)
    assert excinfo.value.error_code == "STATE_MACHINE_PLAN_MARKER_MISSING"
    assert "acceptance" in (excinfo.value.hint or "")
