"""Append-only per-session wake logs for PersonaAgentClient."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_SESSION_LOG_DIR = Path(".map/runtime-waker-sessions")
RESPONSE_PREVIEW_MAX = 200

_SAFE_SESSION_ID_RE = re.compile(r"[^\w.\-]+")


def safe_session_log_name(session_id: str) -> str:
    """Turn a session id into a single path segment safe for log filenames."""
    safe = _SAFE_SESSION_ID_RE.sub("_", session_id.strip())
    return safe or "unknown"


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
) -> Path:
    """Append one JSON line to ``<log_dir>/<session_id>.jsonl``."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{safe_session_log_name(session_id)}.jsonl"
    entry: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "session_id": session_id,
        "persona": persona,
        "integration": integration,
        "status": status,
        "prompt": prompt,
        "response_preview": response_preview(response_text, max_chars=preview_chars),
        "response_chars": len(response_text),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


__all__ = [
    "DEFAULT_SESSION_LOG_DIR",
    "RESPONSE_PREVIEW_MAX",
    "append_session_wake_log",
    "response_preview",
    "safe_session_log_name",
]
