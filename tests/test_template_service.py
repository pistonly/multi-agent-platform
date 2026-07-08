"""b72d0542 I1.a — Result submission 4-段 template soft validator tests.

Verifies the 4 acceptance (a) cases from plan
``docs/MAP-RESULT-SUBMISSION-TEMPLATE.md``:

| Case | input                                | warnings                                |
|------|--------------------------------------|-----------------------------------------|
| 1    | 完整 4 段 + 实施 log 含合法链接      | []                                      |
| 2    | 缺 summary                           | [MISSING_TEMPLATE_SECTION x 1]          |
| 3    | 缺 acceptance 清单                   | [MISSING_TEMPLATE_SECTION x 1]          |
| 4    | 实施 log 段存在但 markdown link 错误 | [MALFORMED_MARKDOWN_LINK]               |

The validator never blocks — ``result.valid`` is always True and the
service layer raises no exception on malformed input.
"""

from __future__ import annotations

from server.services.template_service import (
    validate_result_submission_template,
)

_COMPLETE_RESULT = (
    "# 实验 b72d0542 result\n\n"
    "## summary\n"
    "4 段模板就绪，相似度检测 / --force flag / audit log 全部落地\n\n"
    "## 实施 log\n"
    "- [W45 I1.a](.map/generated-plans/experiment-b72d0542-i1-a-log.md)\n"
    "- [W46 I1.b](.map/generated-plans/experiment-b72d0542-i1-b-log.md)\n\n"
    "## 风险\n"
    "- 已知风险 1: sentence-transformers 冷启动 ≤10s 依赖 CI fixture\n"
    "- 已知风险 2: --force flag 滥用风险由 audit log 兜底\n\n"
    "## acceptance\n"
    "- [x] (a) 4 段模板 + Pydantic schema 校验\n"
    "- [x] (b) embedding 相似度检测 + --force flag\n"
    "- [x] (e) CLI warning + audit log\n"
)


def _filter_codes(result) -> list[str]:
    return [w.code for w in result.warnings]


# --- case 1: complete 4 段 + valid link -----------------------------------


def test_case_1_all_sections_present_emits_no_warnings() -> None:
    result = validate_result_submission_template(_COMPLETE_RESULT)

    assert result.warnings == []
    assert result.sections_present == ("summary", "实施 log", "风险", "acceptance")
    assert result.log_link_count == 2
    assert result.valid is True


# --- case 2: 缺 summary ---------------------------------------------------


def test_case_2_missing_summary_emits_missing_template_section_warning() -> None:
    body = (
        "## 实施 log\n"
        "- [W45](file.md)\n\n"
        "## 风险\n"
        "x\n\n"
        "## acceptance\n"
        "- [x] (a)\n"
    )
    result = validate_result_submission_template(body)

    codes = _filter_codes(result)
    assert "MISSING_TEMPLATE_SECTION" in codes
    missing_sections = {
        w.section for w in result.warnings if w.code == "MISSING_TEMPLATE_SECTION"
    }
    assert "summary" in missing_sections
    assert result.valid is True


# --- case 3: 缺 acceptance ------------------------------------------------


def test_case_3_missing_acceptance_emits_missing_template_section_warning() -> None:
    body = (
        "## summary\n"
        "x\n\n"
        "## 实施 log\n"
        "- [W45](file.md)\n\n"
        "## 风险\n"
        "x\n"
    )
    result = validate_result_submission_template(body)

    codes = _filter_codes(result)
    assert "MISSING_TEMPLATE_SECTION" in codes
    missing_sections = {
        w.section for w in result.warnings if w.code == "MISSING_TEMPLATE_SECTION"
    }
    assert "acceptance" in missing_sections
    assert result.valid is True


# --- case 4: 实施 log 段存在但 link 格式错误 ------------------------------


def test_case_4_malformed_markdown_link_emits_warning() -> None:
    """Log section must contain at least one well-formed link for the
    malformed-fragment detector to surface. (When the section has zero
    valid links, the validator emits NO_LINK_IN_LOG_SECTION — different
    code, different triage.)
    """
    body = (
        "## summary\n"
        "x\n\n"
        "## 实施 log\n"
        "- [valid](path.md)\n"
        "- text [broken without close\n\n"
        "## 风险\n"
        "x\n\n"
        "## acceptance\n"
        "- [x] (a)\n"
    )
    result = validate_result_submission_template(body)

    codes = _filter_codes(result)
    assert "MALFORMED_MARKDOWN_LINK" in codes
    malformed = [w for w in result.warnings if w.code == "MALFORMED_MARKDOWN_LINK"]
    assert len(malformed) == 1
    assert malformed[0].section == "实施 log"
    assert malformed[0].detail is not None
    assert malformed[0].detail.startswith("[")
    assert result.valid is True


def test_case_4_log_section_with_no_link_emits_no_link_warning() -> None:
    """Distinct from case 4: section present but empty (no link at all).

    Triggers ``NO_LINK_IN_LOG_SECTION`` instead of
    ``MALFORMED_MARKDOWN_LINK`` (different code, easier for reviewers
    to triage).
    """
    body = (
        "## summary\nx\n\n"
        "## 实施 log\n"
        "本节尚无具体日志\n\n"
        "## 风险\nx\n\n"
        "## acceptance\n- [x]\n"
    )
    result = validate_result_submission_template(body)

    codes = _filter_codes(result)
    assert "NO_LINK_IN_LOG_SECTION" in codes
    assert "MALFORMED_MARKDOWN_LINK" not in codes
    no_link = [w for w in result.warnings if w.code == "NO_LINK_IN_LOG_SECTION"]
    assert no_link[0].section == "实施 log"
    assert result.valid is True


# --- soft validation invariant --------------------------------------------


def test_validator_never_blocks_valid_is_always_true() -> None:
    """``result.valid`` is True regardless of warning count."""
    for body in (None, "", "## summary\n", _COMPLETE_RESULT):
        result = validate_result_submission_template(body)  # type: ignore[arg-type]
        assert result.valid is True


def test_empty_content_marks_all_sections_missing() -> None:
    result = validate_result_submission_template("")
    missing = {
        w.section
        for w in result.warnings
        if w.code == "MISSING_TEMPLATE_SECTION"
    }
    assert missing == {"summary", "实施 log", "风险", "acceptance"}


def test_none_content_marks_all_sections_missing() -> None:
    result = validate_result_submission_template(None)
    missing = {
        w.section
        for w in result.warnings
        if w.code == "MISSING_TEMPLATE_SECTION"
    }
    assert missing == {"summary", "实施 log", "风险", "acceptance"}


# --- regression: well-formed result log produces no warnings --------------


def test_complete_result_with_single_log_link_passes() -> None:
    body = (
        "## summary\nx\n"
        "## 实施 log\n- [only one](path.md)\n"
        "## 风险\nx\n"
        "## acceptance\n- [x] (a)\n"
    )
    result = validate_result_submission_template(body)
    assert result.warnings == []
    assert result.log_link_count == 1
