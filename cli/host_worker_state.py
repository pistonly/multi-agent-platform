from __future__ import annotations

from pathlib import Path
from typing import Any

from cli.bridge_state import load_bridge_state, save_bridge_state


def load_host_state(path: Path | None) -> dict[str, Any]:
    return load_bridge_state(path, bridge_name="host", default_collections=("topics", "experiments"))


def save_host_state(path: Path | None, state: dict[str, Any]) -> None:
    save_bridge_state(path, state)
