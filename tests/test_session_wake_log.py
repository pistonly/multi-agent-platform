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


def test_append_session_wake_log_includes_d5_join_keys(tmp_path: Path) -> None:
    """D5: event_id + event_source land in the jsonl entry so A3 can join.

    Without these fields the sessions jsonl has no FK to inbound_event /
    notification, so the audit three-way join cannot be proven.
    """
    path = append_session_wake_log(
        log_dir=tmp_path,
        session_id="sid-2",
        persona="host",
        integration="waker",
        prompt="wake",
        response_text="ok",
        status="ok",
        event_id="11111111-1111-1111-1111-111111111111",
        event_source="polling",
        fingerprint="host:pending_topic_reply:topic-1:comment-1",
    )
    entry = json.loads(path.read_text(encoding="utf-8").strip())
    assert entry["event_id"] == "11111111-1111-1111-1111-111111111111"
    assert entry["event_source"] == "polling"
    assert entry["fingerprint"] == "host:pending_topic_reply:topic-1:comment-1"


def test_append_session_wake_log_defaults_event_source_to_polling(tmp_path: Path) -> None:
    """D5 default: Phase 1 emits only polling — explicit default avoids silent sse."""
    path = append_session_wake_log(
        log_dir=tmp_path,
        session_id="sid-3",
        persona="host",
        integration="waker",
        prompt="wake",
        response_text="ok",
        status="ok",
    )
    entry = json.loads(path.read_text(encoding="utf-8").strip())
    assert entry["event_source"] == "polling"
    assert entry["event_id"] is None
    assert entry["fingerprint"] is None
