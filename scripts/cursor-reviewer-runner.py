#!/usr/bin/env python3
"""MAP reviewer bridge runner backed by Cursor SDK."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVIEWER_SKILL_REL = ".cursor/skills/experiment-reviewer/SKILL.md"
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
    experiment = ctx.get("experiment") or {}
    plan_md = ctx.get("plan_md") or ""

    return f"""You are a MAP **reviewer** for project `{PROJECT_ROOT.name}`.
Invoked by the reviewer bridge. **Do not** run shell or `map` CLI.
Return **only** one JSON object.

Read `{REVIEWER_SKILL_REL}` (project settings via SDK).

## Request
- experiment_id: {req.get("experiment_id")}
- dry_run: {req.get("dry_run", False)}

## Experiment
{json.dumps(experiment, ensure_ascii=False, indent=2)}

## Plan (markdown)
{plan_md[:12000]}

## Task
Produce a structured review. Each item must be specific and actionable.

## Required JSON
{{
  "reasonable_items": ["..."],
  "unreasonable_items": ["..."]
}}
"""


def _extract_json(text: str) -> dict:
    """Extract the last non-empty top-level JSON object from agent output.

    Robust against leading/trailing thinking traces that bundled Claude
    Code CLI 2.1.191+ emits into TextBlock.text alongside the response.
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError("Empty agent output")

    decoder = json.JSONDecoder()

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict) and parsed:
            return parsed
    except json.JSONDecodeError:
        pass

    for i in range(len(stripped) - 1, -1, -1):
        if stripped[i] != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(stripped, i)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj:
            return obj

    match = JSON_BLOCK_RE.search(stripped)
    if match:
        parsed = json.loads(match.group(1))
        if isinstance(parsed, dict) and parsed:
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

    for key in ("reasonable_items", "unreasonable_items"):
        if key not in payload or not isinstance(payload[key], list):
            print(f"Missing or invalid {key}", file=sys.stderr)
            sys.exit(1)

    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
