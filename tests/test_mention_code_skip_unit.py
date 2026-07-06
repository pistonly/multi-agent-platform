"""Unit tests for markdown-aware mention extraction (no API client)."""

from server.services.mention_service import extract_mention_names


def test_extract_skips_inline_code():
    body = "工具 `pytest` 与 `@host` 不应匹配"
    assert extract_mention_names(body) == []


def test_extract_skips_fenced_code():
    body = "```\n@multi-agents-platform-host\n`@host`\n```\n块外 @foo-bar"
    assert extract_mention_names(body) == ["foo-bar"]


def test_extract_finds_outside_code():
    body = "请 @reviewer-agent 参与"
    assert extract_mention_names(body) == ["reviewer-agent"]
