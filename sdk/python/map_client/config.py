import os
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or Path.home() / ".map" / "config.yaml"
    config: dict[str, Any] = {}
    if path.exists():
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    api_url = config.get("api_url") or os.environ.get("MAP_API_URL", "http://localhost:18400")
    token = config.get("token") or os.environ.get("MAP_TOKEN")
    project_key = config.get("project_key") or os.environ.get("MAP_PROJECT_KEY")
    return {
        "api_url": str(api_url).rstrip("/"),
        "token": token,
        "project_key": project_key,
        "config_path": path,
    }
