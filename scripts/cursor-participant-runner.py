#!/usr/bin/env python3
"""MAP participant bridge runner backed by Cursor SDK.

Reads bridge request JSON from stdin, returns {body, parent_id} JSON on stdout.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOPIC_PARTICIPANT_SKILL_REL = ".cursor/skills/topic-participant/SKILL.md"
HOME = Path.home()

JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
EXPORT_RE = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _strip_export_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    if value and value[0] not in {"'", '"'}:
        value = re.sub(r"\s+#.*$", "", value).strip()
    return value


def _read_export_from_file(path: Path, name: str) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        match = EXPORT_RE.match(line)
        if match and match.group(1) == name:
            value = _strip_export_value(match.group(2))
            if value:
                return value
    return None


def _resolve_env(name: str, *, default: str | None = None) -> str | None:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    for path in (HOME / ".bashrc", HOME / ".profile", HOME / ".bash_profile"):
        found = _read_export_from_file(path, name)
        if found:
            return found
    return default


def _build_prompt(req: dict) -> str:
    ctx = req.get("context") or {}
    topic = ctx.get("topic") or {}
    reason = ctx.get("reason", "initial")
    reply_to = ctx.get("reply_to")
    my_prior = ctx.get("my_prior_comments") or []

    return f"""You are a MAP **participant** agent for project `{PROJECT_ROOT.name}`.
Invoked by the participant bridge. **Do not** run shell commands or call `map` CLI.
Return **only** one JSON object.

Read and follow `{TOPIC_PARTICIPANT_SKILL_REL}` (project settings loaded via SDK).

## Request
- action: {req.get("action")}
- reason: {reason}
- topic_id: {req.get("topic_id")}
- dry_run: {req.get("dry_run", False)}

## Topic
{json.dumps(topic, ensure_ascii=False, indent=2)}

## Reply target
{json.dumps(reply_to, ensure_ascii=False, indent=2) if reply_to else "null (top-level comment)"}

## Your prior comments on this topic
{json.dumps(my_prior, ensure_ascii=False, indent=2)}

## Task
- `initial`: first substantive opinion on this open topic.
- `follow_up`: respond to the reply target (host summary or peer comment).
- `mention`: answer the @mention in reply target excerpt.

Do **not** post Round N Summary. Do **not** propose creating experiments.

## Required JSON
{{
  "body": "Markdown comment",
  "parent_id": "<comment uuid or null for top-level>"
}}
"""


def _extract_json(text: str) -> dict:
    stripped = text.strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = JSON_BLOCK_RE.search(stripped)
    if match:
        parsed = json.loads(match.group(1))
        if isinstance(parsed, dict):
            return parsed
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        parsed = json.loads(stripped[start : end + 1])
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("Could not parse JSON from agent output")


def main() -> None:
    api_key = _resolve_env("CURSOR_API_KEY")
    if not api_key:
        print("CURSOR_API_KEY not found", file=sys.stderr)
        sys.exit(1)

    try:
        req = json.loads(sys.stdin.readline())
    except (json.JSONDecodeError, EOFError) as exc:
        print(f"Invalid stdin JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions
    except ImportError:
        print("Install cursor-sdk: pip install cursor-sdk", file=sys.stderr)
        sys.exit(1)

    prompt = _build_prompt(req)
    model = _resolve_env("CURSOR_MODEL", default="composer-2.5") or "composer-2.5"

    try:
        result = Agent.prompt(
            prompt,
            AgentOptions(
                api_key=api_key,
                model=model,
                local=LocalAgentOptions(cwd=str(PROJECT_ROOT), setting_sources=["project"]),
            ),
        )
    except CursorAgentError as exc:
        print(f"Cursor startup failed: {exc}", file=sys.stderr)
        sys.exit(1)

    if result.status == "error":
        print(f"Cursor run failed: {getattr(result, 'id', 'unknown')}", file=sys.stderr)
        sys.exit(1)

    raw = (result.result or "").strip()
    if not raw:
        print("Cursor returned empty result", file=sys.stderr)
        sys.exit(1)

    try:
        payload = _extract_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"Invalid agent JSON: {exc}\n---\n{raw[:2000]}", file=sys.stderr)
        sys.exit(1)

    reply_to = (req.get("context") or {}).get("reply_to") or {}
    if payload.get("parent_id") is None and reply_to.get("comment_id"):
        payload["parent_id"] = reply_to["comment_id"]

    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
