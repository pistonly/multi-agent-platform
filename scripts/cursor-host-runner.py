#!/usr/bin/env python3
"""MAP host bridge runner backed by Cursor SDK (local agent).

Reads one JSON request line from stdin (bridge contract v0), calls Cursor Agent.prompt,
prints one JSON response line to stdout.

Credentials (first match wins):
  1. CURSOR_API_KEY / CURSOR_MODEL environment variables
  2. export lines in ~/.bashrc, ~/.profile, ~/.bash_profile (non-interactive safe)

Install:
  pip install cursor-sdk

Do not write MAP from this script — the bridge performs host persona writes.

Project skills/rules load via Cursor SDK `local.setting_sources=["project"]`.
The prompt points the agent at topic-host skill; it is not inlined.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOPIC_HOST_SKILL_REL = ".cursor/skills/topic-host/SKILL.md"
EXPERIMENT_HOST_SKILL_REL = ".cursor/skills/experiment-host/SKILL.md"
HOME = Path.home()

JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
EXPORT_RE = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _strip_export_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    # Inline comment only when value is unquoted.
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
    action = req.get("action", "reply_pending")
    ctx = req.get("context") or {}
    pending = ctx.get("pending_item") or {}
    topic = ctx.get("topic") or {}
    experiment = ctx.get("experiment") or {}
    round_n = ctx.get("round_n")
    participant_comments = ctx.get("participant_comments") or []
    plan_md = ctx.get("plan_md") or ""
    unreasonable_items = ctx.get("unreasonable_items") or []
    git_before = ctx.get("git_checkpoint_before")

    skill = EXPERIMENT_HOST_SKILL_REL if action in {"revise_plan", "execute_experiment"} else TOPIC_HOST_SKILL_REL

    task_block = {
        "reply_pending": """For `reply_pending`: draft a concise Markdown host reply to the pending thread.
Respect discussion_round / round_summary_count. Do not post a full Round Summary here unless explicitly closing the round.""",
        "round_summary": f"""For `round_summary`: draft a **top-level** Markdown post for **Round {round_n} Summary**.
Use the template from the skill (`## Round {round_n} Summary`, 已共识 / 未决 / 下轮议程 / 主持状态).
Set parent_id to null. Set advance_round to true after posting.""",
        "promote_experiment": """For `promote_experiment`: first distill the topic into structured conclusion fields:
decision, rationale, rejected_options, open_questions, and action_items.
Then draft an experiment plan in Markdown (use as body). Set create_experiment to true. parent_id should be null.""",
        "revise_plan": """For `revise_plan`: revise the experiment plan Markdown to address all open unreasonable items.
Return full revised plan as `body` and a short `change_note`. Do not call map CLI.""",
        "execute_experiment": """For `execute_experiment`: implement the plan in the repository (edit files as needed).
**Do not run git commit** — the bridge already checkpointed before this run.
Return `summary` (one line) and `execution_log_md` (detailed log with files changed and verification).""",
    }.get(action, "Follow the host skill for this action.")

    if action in {"revise_plan", "execute_experiment"}:
        json_shape = """{
  "summary": "one-line conclusion (execute_experiment only)",
  "execution_log_md": "Markdown log (execute_experiment only)",
  "body": "revised plan Markdown (revise_plan only)",
  "change_note": "short note (revise_plan only)"
}"""
    else:
        if action == "promote_experiment":
            json_shape = """{
  "body": "experiment plan Markdown",
  "parent_id": null,
  "advance_round": false,
  "create_experiment": true,
  "decision": "final topic conclusion before creating the experiment",
  "rationale": "why this conclusion follows from the discussion",
  "rejected_options": "alternatives not chosen, or null",
  "open_questions": "questions carried into the experiment, or null",
  "no_decision_reason": null,
  "action_items": [
    {
      "title": "short actionable follow-up",
      "description": "optional detail",
      "owner_agent_id": null,
      "due_at": null,
      "linked_experiment_id": null
    }
  ]
}"""
        else:
            json_shape = """{
  "body": "Markdown reply or summary text",
  "parent_id": "<comment_id or null>",
  "advance_round": false,
  "create_experiment": false
}"""

    return f"""You are the MAP host for project `{PROJECT_ROOT.name}`.
You are invoked by the host bridge runner. **Do not** call `map` CLI for MAP writes (bridge handles that).
Return **only** a single JSON object (no markdown prose outside the JSON).

## Host behavior
Read and follow `{skill}` before acting. Project `.cursor/` settings are loaded via SDK.

## Bridge request
- action: {action}
- topic_id: {req.get("topic_id")}
- experiment_id: {req.get("experiment_id")}
- dry_run: {req.get("dry_run", False)}

## Topic snapshot
{json.dumps(topic, ensure_ascii=False, indent=2)}

## Experiment snapshot
{json.dumps(experiment, ensure_ascii=False, indent=2)}

## Plan (markdown)
{plan_md[:12000]}

## Unreasonable review items (revise_plan)
{json.dumps(unreasonable_items, ensure_ascii=False, indent=2)}

## Git checkpoint before (execute_experiment)
{git_before or "n/a"}

## Pending item
{json.dumps(pending, ensure_ascii=False, indent=2)}

## Participant comments (round_summary)
{json.dumps(participant_comments, ensure_ascii=False, indent=2)}

## Your task
{task_block}

## Required stdout JSON shape
{json_shape}

Output **one** JSON object only.
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
        print(
            "CURSOR_API_KEY not found in environment or ~/.bashrc / ~/.profile / ~/.bash_profile",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        req = json.loads(sys.stdin.readline())
    except (json.JSONDecodeError, EOFError) as exc:
        print(f"Invalid stdin JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(req, dict):
        print("stdin payload must be a JSON object", file=sys.stderr)
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
                local=LocalAgentOptions(
                    cwd=str(PROJECT_ROOT),
                    setting_sources=["project"],
                ),
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

    pending = (req.get("context") or {}).get("pending_item") or {}
    action = req.get("action", "reply_pending")
    if action in {"reply_pending", "round_summary", "promote_experiment"}:
        if payload.get("parent_id") is None and pending.get("comment_id"):
            payload["parent_id"] = pending["comment_id"]
        payload.setdefault("advance_round", False)
        payload.setdefault("create_experiment", False)

    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
