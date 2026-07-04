"""Tests for cli.session_wake_log."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cli.session_wake_log import (
    append_session_event,
    append_session_wake_log,
    log_now,
    resolve_session_log_path,
    response_preview,
    safe_session_log_name,
)

_STAMPED_NAME_RE = re.compile(
    r"^\d{8}-\d{6}_[\w.\-]+_[\w.\-]+\.jsonl$"
)


def test_safe_session_log_name_replaces_unsafe_chars() -> None:
    assert safe_session_log_name("abc-123") == "abc-123"
    assert safe_session_log_name("sess/with:chars") == "sess_with_chars"


def test_response_preview_truncates_to_max_chars() -> None:
    assert response_preview("hello", max_chars=3) == "hel"
    assert response_preview("", max_chars=200) == ""


def test_resolve_session_log_path_creates_timestamped_name(tmp_path: Path) -> None:
    path = resolve_session_log_path(tmp_path, "sid-1", "host")
    assert path.parent == tmp_path
    assert _STAMPED_NAME_RE.match(path.name)
    assert path.name.endswith("_host_sid-1.jsonl")


def test_resolve_session_log_path_reuses_existing_file(tmp_path: Path) -> None:
    first = resolve_session_log_path(tmp_path, "sid-1", "host")
    first.write_text("{}\n", encoding="utf-8")
    second = resolve_session_log_path(tmp_path, "sid-1", "host")
    assert second == first


def test_resolve_session_log_path_does_not_reuse_other_persona(tmp_path: Path) -> None:
    """Same provisional session id across personas must not share one log file."""
    host_path = resolve_session_log_path(tmp_path, "new-20260703T075815", "host")
    host_path.write_text("{}\n", encoding="utf-8")
    participant_path = resolve_session_log_path(tmp_path, "new-20260703T075815", "participant")
    assert participant_path != host_path
    assert participant_path.name.endswith("_participant_new-20260703T075815.jsonl")


def test_resolve_session_log_path_reuses_legacy_filename(tmp_path: Path) -> None:
    legacy = tmp_path / "sid-1.jsonl"
    legacy.write_text("{}\n", encoding="utf-8")
    assert resolve_session_log_path(tmp_path, "sid-1", "host") == legacy


def test_append_session_wake_log_writes_jsonl(tmp_path: Path, monkeypatch) -> None:
    fixed = datetime(2026, 7, 2, 11, 30, 45, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setenv("MAP_LOG_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setattr("cli.session_wake_log.log_now", lambda: fixed)

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
    assert _STAMPED_NAME_RE.match(path.name)
    assert path.name.endswith("_host_sid-1.jsonl")
    entry = json.loads(path.read_text(encoding="utf-8").strip())
    assert entry["prompt"] == "do work"
    assert entry["response_preview"] == "abcdefghij"
    assert entry["response_chars"] == 16
    assert entry["ts"] == "2026-07-02T11:30:45+08:00"


def test_log_now_uses_configured_timezone(monkeypatch) -> None:
    monkeypatch.setenv("MAP_LOG_TIMEZONE", "Asia/Shanghai")
    now = log_now()
    assert now.tzinfo == ZoneInfo("Asia/Shanghai")
    assert now.utcoffset().total_seconds() == 8 * 3600


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


def test_append_session_event_writes_live_event_line(tmp_path: Path, monkeypatch) -> None:
    fixed = datetime(2026, 7, 2, 11, 30, 45, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr("cli.session_wake_log.log_now", lambda: fixed)

    # Parent dir does not exist yet — append_session_event must create it.
    log_path = tmp_path / "sub" / "session.jsonl"
    append_session_event(
        log_path=log_path,
        persona="host",
        integration="waker",
        event="tool_use",
        summary="Bash: map todos",
        event_id="22222222-2222-2222-2222-222222222222",
        fingerprint="host:pending_topic_replies:c1",
    )
    assert log_path.is_file()
    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["event"] == "tool_use"
    assert entry["summary"] == "Bash: map todos"
    assert entry["persona"] == "host"
    assert entry["integration"] == "waker"
    assert entry["ts"] == "2026-07-02T11:30:45+08:00"
    assert entry["event_id"] == "22222222-2222-2222-2222-222222222222"
    assert entry["fingerprint"] == "host:pending_topic_replies:c1"
