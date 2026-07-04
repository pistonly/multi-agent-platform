"""Append-only per-session wake logs for PersonaAgentClient."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_SESSION_LOG_DIR = Path(".map/runtime-waker-sessions")
DEFAULT_LOG_TIMEZONE = "Asia/Shanghai"
RESPONSE_PREVIEW_MAX = 200

_SAFE_SESSION_ID_RE = re.compile(r"[^\w.\-]+")


def log_timezone() -> ZoneInfo:
    """Timezone for human-readable session log timestamps (default: Asia/Shanghai)."""
    return ZoneInfo(os.environ.get("MAP_LOG_TIMEZONE", DEFAULT_LOG_TIMEZONE))


def log_now() -> datetime:
    return datetime.now(log_timezone())


def safe_session_log_name(session_id: str) -> str:
    """Turn a session id into a single path segment safe for log filenames."""
    safe = _SAFE_SESSION_ID_RE.sub("_", session_id.strip())
    return safe or "unknown"


def resolve_session_log_path(log_dir: Path, session_id: str, persona: str) -> Path:
    """Resolve the JSONL path for a session (reuse existing file across resume wakes)."""
    safe_id = safe_session_log_name(session_id)
    safe_persona = safe_session_log_name(persona)
    # Persona must be part of the lookup — multiple wakers can share the same
    # provisional ``new-YYYYMMDDTHHMMSS`` id when they wake in the same second.
    stamped = sorted(
        log_dir.glob(f"*_{safe_persona}_{safe_id}.jsonl"),
        key=lambda path: path.stat().st_mtime,
    )
    if stamped:
        return stamped[-1]
    legacy = log_dir / f"{safe_id}.jsonl"
    if legacy.exists():
        return legacy
    ts = log_now().strftime("%Y%m%d-%H%M%S")
    return log_dir / f"{ts}_{safe_persona}_{safe_id}.jsonl"


def response_preview(text: str, *, max_chars: int = RESPONSE_PREVIEW_MAX) -> str:
    if max_chars <= 0:
        return ""
    return text[:max_chars]


EVENT_SUMMARY_MAX = 160

# Scalar input fields that best describe what a tool call is doing, in priority
# order — keeps tool_use summaries short and human-scannable (which command,
# which file) instead of dumping the whole input dict.
_TOOL_INPUT_PRIORITY: tuple[str, ...] = (
    "command",
    "description",
    "file_path",
    "path",
    "pattern",
    "url",
    "query",
    "body",
)


def _scalar_preview(value: Any, *, max_chars: int) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:max_chars]
    return json.dumps(value, ensure_ascii=False)[:max_chars]


def text_summary(text: str | None, *, max_chars: int = EVENT_SUMMARY_MAX) -> str:
    """One-line preview of assistant text (newlines collapsed)."""
    if not text:
        return ""
    return " ".join(text.split())[:max_chars]


def tool_use_summary(
    name: str,
    tool_input: Any,
    *,
    max_chars: int = EVENT_SUMMARY_MAX,
) -> str:
    """Short summary of a tool call, e.g. ``Bash: pytest tests/ -q``."""
    if isinstance(tool_input, dict):
        for key in _TOOL_INPUT_PRIORITY:
            if tool_input.get(key) is not None:
                return f"{name}: {_scalar_preview(tool_input[key], max_chars=max_chars)}"
        for value in tool_input.values():
            if isinstance(value, (str, int, float, bool)):
                return f"{name}: {_scalar_preview(value, max_chars=max_chars)}"
        return str(name)
    return f"{name}: {_scalar_preview(tool_input, max_chars=max_chars)}"


def tool_result_summary(
    content: Any,
    is_error: bool | None,
    *,
    max_chars: int = EVENT_SUMMARY_MAX,
) -> str:
    """Short summary of a tool result, e.g. ``ok: 45 passed`` or ``error: ...``."""
    label = "error" if is_error else "ok"
    if content is None:
        return label
    if isinstance(content, str):
        body = content
    elif isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        body = " ".join(p for p in parts if p)
    else:
        body = str(content)
    body = " ".join(body.split())
    if not body:
        return label
    return f"{label}: {body[:max_chars]}"


def append_session_wake_log(
    *,
    log_dir: Path,
    session_id: str,
    persona: str,
    integration: str,
    prompt: str,
    response_text: str,
    status: str,
    preview_chars: int = RESPONSE_PREVIEW_MAX,
    event_id: str | None = None,
    event_source: str = "polling",
    fingerprint: str | None = None,
    log_path: Path | None = None,
) -> Path:
    """Append one JSON line to the session's JSONL file under ``log_dir``.

    ``event_id`` and ``event_source`` are the join keys for the A3 audit
    three-way join (notification ↔ inbound_event ↔ sessions jsonl). Default
    ``event_source="polling"`` matches Phase 1 reality; Phase 2 will start
    emitting ``sse`` once the event-driven path is wired.
    ``fingerprint`` is optional — included when the caller has it on hand so
    A3 join fallbacks can pivot on it without consulting inbound_event.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    # When the caller already resolved a log_path (e.g. wake_up writes live
    # events into one file and wants the result summary in that same file),
    # honor it instead of re-resolving by session_id.
    path = log_path if log_path is not None else resolve_session_log_path(log_dir, session_id, persona)
    entry: dict[str, Any] = {
        "ts": log_now().isoformat(),
        "session_id": session_id,
        "persona": persona,
        "integration": integration,
        "status": status,
        "event_source": event_source,
        "event_id": event_id,
        "fingerprint": fingerprint,
        "prompt": prompt,
        "response_preview": response_preview(response_text, max_chars=preview_chars),
        "response_chars": len(response_text),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def append_session_event(
    *,
    log_path: Path,
    persona: str,
    integration: str,
    event: str,
    summary: str,
    event_id: str | None = None,
    event_source: str = "polling",
    fingerprint: str | None = None,
) -> None:
    """Append one real-time event line to a session's JSONL file.

    Called live during ``PersonaAgentClient.wake_up()`` for each assistant text
    block, tool call, and tool result, so an agent stuck mid-turn is visible
    from the timestamp of the last written event. ``event`` is one of
    ``wake``/``text``/``tool_use``/``tool_result``/``result``. The caller
    resolves ``log_path`` once up front (via :func:`resolve_session_log_path`)
    so every event of one wake lands in the same file.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {
        "ts": log_now().isoformat(),
        "persona": persona,
        "integration": integration,
        "event": event,
        "summary": summary,
        "event_source": event_source,
        "event_id": event_id,
        "fingerprint": fingerprint,
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


__all__ = [
    "DEFAULT_LOG_TIMEZONE",
    "DEFAULT_SESSION_LOG_DIR",
    "EVENT_SUMMARY_MAX",
    "RESPONSE_PREVIEW_MAX",
    "append_session_event",
    "append_session_wake_log",
    "log_now",
    "log_timezone",
    "resolve_session_log_path",
    "response_preview",
    "safe_session_log_name",
    "text_summary",
    "tool_result_summary",
    "tool_use_summary",
]
