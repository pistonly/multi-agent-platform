from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cli.host_worker_types import WorkerError


def load_host_state(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"schema_version": 1, "topics": {}, "experiments": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkerError(f"Invalid host bridge state file: {path}") from exc
    if not isinstance(state, dict):
        raise WorkerError(f"Invalid host bridge state file: {path}")
    if state.get("schema_version", 1) != 1:
        raise WorkerError(f"Unsupported host bridge state schema: {state.get('schema_version')}")
    state.setdefault("schema_version", 1)
    state.setdefault("topics", {})
    state.setdefault("experiments", {})
    return state


def save_host_state(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)
