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
    stamped = sorted(log_dir.glob(f"*_{safe_id}.jsonl"), key=lambda path: path.stat().st_mtime)
    if stamped:
        return stamped[-1]
    legacy = log_dir / f"{safe_id}.jsonl"
    if legacy.exists():
        return legacy
    ts = log_now().strftime("%Y%m%d-%H%M%S")
    safe_persona = safe_session_log_name(persona)
    return log_dir / f"{ts}_{safe_persona}_{safe_id}.jsonl"


def response_preview(text: str, *, max_chars: int = RESPONSE_PREVIEW_MAX) -> str:
    if max_chars <= 0:
        return ""
    return text[:max_chars]


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
    path = resolve_session_log_path(log_dir, session_id, persona)
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


__all__ = [
    "DEFAULT_LOG_TIMEZONE",
    "DEFAULT_SESSION_LOG_DIR",
    "RESPONSE_PREVIEW_MAX",
    "append_session_wake_log",
    "log_now",
    "log_timezone",
    "resolve_session_log_path",
    "response_preview",
    "safe_session_log_name",
]
