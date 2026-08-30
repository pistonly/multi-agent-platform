"""Tests for cli.session_wake_log."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cli.session_wake_log import (
    SessionWakeLogger,
    append_session_event,
    append_session_wake_log,
    log_now,
    provisional_session_id,
    rename_session_log_for_id,
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


def test_provisional_session_id_uses_log_timezone(monkeypatch) -> None:
    """临时 id 与文件名 ts 前缀必须同一时钟——一个文件名里不允许混 UTC/东八区。"""
    fixed = datetime(2026, 8, 30, 12, 0, 0, 123456, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr("cli.session_wake_log.log_now", lambda: fixed)
    assert provisional_session_id() == "new-20260830T120000123456"
    assert provisional_session_id("unknown") == "unknown-20260830T120000123456"


def test_rename_session_log_adopts_real_session_id(tmp_path: Path) -> None:
    """首轮 provisional 文件在拿到真实 sid 后改名，且能被 resume 查找命中。"""
    path = resolve_session_log_path(tmp_path, "new-20260830T120000000000", "host")
    path.write_text("{}\n", encoding="utf-8")

    renamed = rename_session_log_for_id(path, "host", "real-sid")

    assert renamed.name.endswith("_host_real-sid.jsonl")
    assert renamed.is_file()
    assert not path.exists()
    # Renamed file must be found by the resume-wake lookup.
    assert resolve_session_log_path(tmp_path, "real-sid", "host") == renamed


def test_rename_session_log_is_noop_when_id_unchanged(tmp_path: Path) -> None:
    path = resolve_session_log_path(tmp_path, "real-sid", "host")
    path.write_text("{}\n", encoding="utf-8")
    assert rename_session_log_for_id(path, "host", "real-sid") == path


def test_rename_session_log_keeps_existing_real_id_file_on_collision(tmp_path: Path) -> None:
    """目标真实 sid 文件已存在时不覆盖：继续写已有文件，provisional 保留原样。"""
    provisional = resolve_session_log_path(tmp_path, "new-20260830T120000000000", "host")
    provisional.write_text("{}\n", encoding="utf-8")
    real = resolve_session_log_path(tmp_path, "real-sid", "host")
    real.write_text('{"existing": true}\n', encoding="utf-8")

    assert rename_session_log_for_id(provisional, "host", "real-sid") == real
    assert real.read_text(encoding="utf-8") == '{"existing": true}\n'


def test_rename_session_log_noop_for_legacy_unstamped_name(tmp_path: Path) -> None:
    """无 ts 前缀的 legacy 文件名不改名（无从保留创建时间戳）。"""
    legacy = tmp_path / "sid-1.jsonl"
    legacy.write_text("{}\n", encoding="utf-8")
    assert rename_session_log_for_id(legacy, "host", "real-sid") == legacy


def test_session_wake_logger_respects_kill_switch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MAP_SESSION_WAKE_LOG", "0")
    log = SessionWakeLogger(
        persona="host",
        integration="waker",
        project_root=tmp_path,
        session_log_dir=tmp_path / "logs",
    )
    assert log.disabled() is True
    assert log.append_wake_log(
        session_id="s", prompt="p", response_text="r", status="ok"
    ) is None
    assert list((tmp_path / "logs").glob("*.jsonl")) == []


def test_session_wake_logger_resolves_dir_precedence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MAP_SESSION_WAKE_LOG_DIR", raising=False)
    log = SessionWakeLogger(persona="host", integration="waker", project_root=tmp_path)
    assert log.resolve_log_dir() == tmp_path / ".map" / "runtime-waker-sessions"
    monkeypatch.setenv("MAP_SESSION_WAKE_LOG_DIR", str(tmp_path / "override"))
    assert log.resolve_log_dir() == tmp_path / "override"


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
