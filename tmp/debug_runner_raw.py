#!/usr/bin/env python3
"""Debug wrapper: dump raw agent output (TextBlock.text concatenated) before
_extract_json parses it. Lets us see exactly what 0.2.110's bundled CLI returns.

Usage: same stdin contract as scripts/claude-host-runner.py.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "claude_host_runner", PROJECT_ROOT / "scripts" / "claude-host-runner.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query  # noqa: E402


async def main() -> None:
    req = json.loads(sys.stdin.readline())
    env, model = runner._build_claude_env()
    runner._require_credentials(env)

    prompt = runner._build_prompt(req)
    options_kwargs = dict(
        cwd=str(PROJECT_ROOT),
        setting_sources=["project"],
        permission_mode="acceptEdits",
        env=env,
    )
    if model:
        options_kwargs["model"] = model
    options = ClaudeAgentOptions(**options_kwargs)

    pieces: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    pieces.append(block.text)

    raw = "".join(pieces)
    Path("/tmp/runner_raw.txt").write_text(raw, encoding="utf-8")

    summary = {
        "length_chars": len(raw),
        "length_lines": raw.count("\n") + 1,
        "first_300": raw[:300],
        "last_500": raw[-500:],
        "extract_json_picked": None,
        "extract_json_error": None,
    }

    try:
        parsed = runner._extract_json(raw)
        summary["extract_json_picked"] = parsed
    except Exception as exc:
        summary["extract_json_error"] = str(exc)

    Path("/tmp/runner_raw.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[debug] raw saved to /tmp/runner_raw.txt ({len(raw)} chars)", file=sys.stderr)
    print(f"[debug] summary saved to /tmp/runner_raw.json", file=sys.stderr)

    try:
        parsed = runner._extract_json(raw)
        print(json.dumps(parsed, ensure_ascii=False, separators=(",", ":")))
    except Exception as exc:
        print(f"_extract_json failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
