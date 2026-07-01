"""Tests for cli.session_wake_log."""

from __future__ import annotations

import json
from pathlib import Path

from cli.session_wake_log import (
    append_session_wake_log,
    response_preview,
    safe_session_log_name,
)


def test_safe_session_log_name_replaces_unsafe_chars() -> None:
    assert safe_session_log_name("abc-123") == "abc-123"
    assert safe_session_log_name("sess/with:chars") == "sess_with_chars"


def test_response_preview_truncates_to_max_chars() -> None:
    assert response_preview("hello", max_chars=3) == "hel"
    assert response_preview("", max_chars=200) == ""


def test_append_session_wake_log_writes_jsonl(tmp_path: Path) -> None:
    path = append_session_wake_log(
        log_dir=tmp_path,
        session_id="sid-1",
        persona="host",
        integration="waker",
        prompt="do work",
        response_text="abcdefghijklmnop",
        status="ok",
        preview_chars=10,
    )
    assert path == tmp_path / "sid-1.jsonl"
    entry = json.loads(path.read_text(encoding="utf-8").strip())
    assert entry["prompt"] == "do work"
    assert entry["response_preview"] == "abcdefghij"
    assert entry["response_chars"] == 16
