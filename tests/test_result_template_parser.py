"""b72d0542 I1.a — Result submission 4-段 template parser unit tests.

Tests the pure parser in ``sdk/python/map_client/result_template.py``
(no service / API involvement). Mirrors the ``test_plan_evidence_parser.py``
shape used for 8ac93d4e I1.a.
"""

from __future__ import annotations

from map_client.result_template import (
    REQUIRED_SECTION_NAMES,
    extract_malformed_link_fragment,
    parse_result_submission,
)

# --- A1: all 4 sections present -------------------------------------------


def test_parse_all_sections_present() -> None:
    content = (
        "# Result\n\n"
        "## summary\n"
        "一键总结：4 段模板就绪\n\n"
        "## 实施 log\n"
        "- [W41 I1.a](.map/generated-plans/experiment-b72d0542-i1-a-log.md)\n"
        "- [W42 I1.b](.map/generated-plans/experiment-b72d0542-i1-b-log.md)\n\n"
        "## 风险\n"
        "- 已知风险: embedding 冷启动 ≤10s 需 CI fixture\n\n"
        "## acceptance\n"
        "- [x] (a) 4 段模板 + Pydantic schema 校验\n"
        "- [ ] (b) embedding 相似度检测\n"
    )
    parsed = parse_result_submission(content)

    assert parsed.summary_present is True
    assert parsed.log_present is True
    assert parsed.risk_present is True
    assert parsed.acceptance_present is True
    assert parsed.log_links == (
        ("W41 I1.a", ".map/generated-plans/experiment-b72d0542-i1-a-log.md"),
        ("W42 I1.b", ".map/generated-plans/experiment-b72d0542-i1-b-log.md"),
    )


def test_parse_section_names_case_insensitive() -> None:
    """## SUMMARY and ##  风险 (double-space) still count as present."""
    content = (
        "## SUMMARY\n"
        "x\n\n"
        "##  风险  \n"
        "y\n\n"
        "## acceptance\n"
        "z\n\n"
        "## 实施 log\n"
        "- [a](b.md)\n"
    )
    parsed = parse_result_submission(content)
    assert parsed.summary_present is True
    assert parsed.risk_present is True


def test_parse_missing_sections_marked_false() -> None:
    parsed = parse_result_submission("## summary\nx\n")
    assert parsed.summary_present is True
    assert parsed.log_present is False
    assert parsed.risk_present is False
    assert parsed.acceptance_present is False


# --- A2: log section body slicing -----------------------------------------


def test_slice_section_body_until_next_heading() -> None:
    """Log section body excludes the next ``##`` heading line."""
    content = (
        "## 实施 log\n"
        "first line\n"
        "second line\n\n"
        "## 风险\n"
        "ignored\n"
    )
    parsed = parse_result_submission(content)
    assert parsed.log_links == ()
    assert parsed.log_present is True


def test_slice_section_body_to_end_when_last_section() -> None:
    content = (
        "## summary\nx\n\n"
        "## 实施 log\n"
        "- [a](b.md)\n"
        "- trailing line\n"
    )
    parsed = parse_result_submission(content)
    assert parsed.log_links == (("a", "b.md"),)


def test_log_links_extract_full_https_urls() -> None:
    content = (
        "## 实施 log\n"
        "- [external](https://example.com/logs/run-42)\n"
    )
    parsed = parse_result_submission(content)
    assert parsed.log_links == (("external", "https://example.com/logs/run-42"),)


# --- A3: malformed link detection -----------------------------------------


def test_extract_malformed_link_unbalanced_open_bracket() -> None:
    body = "text [broken without close\nmore text"
    fragment = extract_malformed_link_fragment(body)
    assert fragment is not None
    assert fragment.startswith("[")


def test_extract_malformed_link_unbalanced_open_paren() -> None:
    body = "text (broken without close\nmore text"
    fragment = extract_malformed_link_fragment(body)
    assert fragment is not None
    assert fragment.startswith("(")


def test_extract_malformed_link_returns_none_when_balanced() -> None:
    body = "- [ok](path.md)\n- [also ok](other.md)\n"
    assert extract_malformed_link_fragment(body) is None


def test_extract_malformed_link_returns_none_when_no_link_like_text() -> None:
    body = "plain text without link fragments\n## next\n"
    assert extract_malformed_link_fragment(body) is None


# --- A4: empty / None -----------------------------------------------------


def test_parse_none_content_returns_all_false() -> None:
    parsed = parse_result_submission(None)
    assert parsed.summary_present is False
    assert parsed.log_present is False
    assert parsed.risk_present is False
    assert parsed.acceptance_present is False
    assert parsed.log_links == ()


def test_parse_empty_string_returns_all_false() -> None:
    parsed = parse_result_submission("")
    assert parsed.summary_present is False


# --- A5: canonical section names constant ---------------------------------


def test_required_section_names_constant_pinned() -> None:
    assert REQUIRED_SECTION_NAMES == ("summary", "实施 log", "风险", "acceptance")
