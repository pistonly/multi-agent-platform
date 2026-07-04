from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Callable
from typing import Any

from cli.host_worker_types import WorkerError


def invoke_agent_runner(
    *,
    agent_runner: str | None,
    runner_timeout: float,
    request: dict[str, Any],
    action: str,
    topic_id: str,
    log_event: Callable[..., None],
) -> tuple[str, dict[str, Any]]:
    if not agent_runner:
        raise WorkerError("agent_runner is not configured")
    cmd = shlex.split(agent_runner)
    if not cmd:
        raise WorkerError("agent_runner is empty")

    payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
    try:
        result = subprocess.run(
            cmd,
            input=payload,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=runner_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log_event(action, topic_id, status="runner_timeout")
        return "error", {}

    if result.returncode == 2:
        log_event(action, topic_id, status="runner_skip", stderr=result.stderr.strip())
        return "skip", {}
    if result.returncode != 0:
        log_event(
            action,
            topic_id,
            status="runner_error",
            exit_code=result.returncode,
            stderr=result.stderr.strip(),
        )
        return "error", {}

    try:
        parsed = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        log_event(action, topic_id, status="runner_invalid_json", stdout=result.stdout.strip())
        return "error", {}
    if not isinstance(parsed, dict):
        log_event(action, topic_id, status="runner_invalid_payload")
        return "error", {}

    for key in ("advance_round", "create_experiment"):
        if key in parsed:
            parsed[key] = bool(parsed[key])
    if parsed.get("body") is not None and not isinstance(parsed["body"], str):
        log_event(action, topic_id, status="runner_invalid_body")
        return "error", {}
    if parsed.get("parent_id") is not None:
        parsed["parent_id"] = str(parsed["parent_id"])
    return "ok", parsed
