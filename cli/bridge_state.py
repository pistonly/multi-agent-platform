from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cli.host_worker_types import WorkerError


def load_bridge_state(
    path: Path | None,
    *,
    bridge_name: str,
    default_collections: tuple[str, ...],
    validate_schema: bool = True,
) -> dict[str, Any]:
    if path is None or not path.exists():
        return _default_state(default_collections)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkerError(f"Invalid {bridge_name} bridge state file: {path}") from exc
    if not isinstance(state, dict):
        raise WorkerError(f"Invalid {bridge_name} bridge state file: {path}")
    if validate_schema and state.get("schema_version", 1) != 1:
        raise WorkerError(f"Unsupported {bridge_name} bridge state schema: {state.get('schema_version')}")
    state.setdefault("schema_version", 1)
    for collection in default_collections:
        state.setdefault(collection, {})
    return state


def save_bridge_state(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _default_state(default_collections: tuple[str, ...]) -> dict[str, Any]:
    return {"schema_version": 1, **{collection: {} for collection in default_collections}}
