#!/usr/bin/env python3
"""MAP reviewer bridge runner backed by Claude Agent SDK.

Reads bridge request JSON from stdin, returns
``{reasonable_items, unreasonable_items}`` JSON on stdout.

Credentials (first match wins):
  1. environment variables (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN, etc.)
  2. export lines in ~/.bashrc, ~/.profile, ~/.bash_profile (non-interactive safe)

Install:
  pip install claude-agent-sdk

Do not write MAP from this script — the bridge performs reviewer persona writes.

Project skills/rules load via ``setting_sources=["project"]``.
"""

from __future__ import annotations

import asyncio
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
    project_env = PROJECT_ROOT / ".map" / ".claude-env"
    for path in (project_env, HOME / ".bashrc", HOME / ".profile", HOME / ".bash_profile"):
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


def _build_claude_env() -> tuple[dict[str, str], str | None]:
    env: dict[str, str] = {}
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        value = _resolve_env(key)
        if value:
            env[key] = value
    model = _resolve_env("ANTHROPIC_MODEL") or _resolve_env("CLAUDE_MODEL")
    return env, model


def _require_credentials(env: dict[str, str]) -> None:
    if "ANTHROPIC_API_KEY" in env or "ANTHROPIC_AUTH_TOKEN" in env:
        return
    print(
        "Neither ANTHROPIC_API_KEY nor ANTHROPIC_AUTH_TOKEN found in environment "
        "or ~/.bashrc / ~/.profile / ~/.bash_profile",
        file=sys.stderr,
    )
    sys.exit(1)


async def _run_query(prompt: str, env: dict[str, str], model: str | None) -> str:
    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ClaudeSDKError,
            TextBlock,
            query,
        )
    except ImportError:
        print("Install claude-agent-sdk: pip install claude-agent-sdk", file=sys.stderr)
        sys.exit(1)

    options_kwargs: dict = dict(
        cwd=str(PROJECT_ROOT),
        setting_sources=["project"],
        permission_mode="acceptEdits",
        env=env,
    )
    if model:
        options_kwargs["model"] = model

    options = ClaudeAgentOptions(**options_kwargs)

    pieces: list[str] = []
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        pieces.append(block.text)
    except ClaudeSDKError as exc:
        print(f"Claude run failed: {exc}", file=sys.stderr)
        sys.exit(1)

    raw = "".join(pieces).strip()
    if not raw:
        print("Claude returned empty response", file=sys.stderr)
        sys.exit(1)
    return raw


def main() -> None:
    try:
        req = json.loads(sys.stdin.readline())
    except (json.JSONDecodeError, EOFError) as exc:
        print(f"Invalid stdin JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(req, dict):
        print("stdin payload must be a JSON object", file=sys.stderr)
        sys.exit(1)

    env, model = _build_claude_env()
    _require_credentials(env)

    prompt = _build_prompt(req)
    raw = asyncio.run(_run_query(prompt, env, model))

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
