"""Project-local MAP identity: `.map/` config + persona tokens."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from map_client.client import MAPClient

MAP_DIR_NAME = ".map"
CONFIG_FILE = "config.yaml"
AGENTS_FILE = "agents.yaml"
AGENTS_LOCAL_FILE = "agents.local.yaml"
DEFAULT_PERSONA = "host"


@dataclass(frozen=True)
class PersonaInfo:
    name: str
    agent_name: str
    description: str | None = None
    role: str | None = None


@dataclass(frozen=True)
class ProjectMapConfig:
    map_dir: Path
    api_url: str
    project_key: str
    project_id: str | None
    default_persona: str
    personas: dict[str, PersonaInfo]
    tokens: dict[str, str]

    def token_for(self, persona: str) -> str:
        if persona not in self.tokens:
            known = ", ".join(sorted(self.tokens)) or "(none)"
            raise ValueError(f"Unknown or missing token for persona '{persona}'. Known: {known}")
        return self.tokens[persona]

    def client_for(self, persona: str | None = None, *, transport: Any = None) -> MAPClient:
        effective = persona or self.default_persona
        return MAPClient(self.api_url, self.token_for(effective), transport=transport)


def find_map_dir(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for path in [current, *current.parents]:
        map_dir = path / MAP_DIR_NAME
        if (map_dir / CONFIG_FILE).is_file():
            return map_dir
    return None


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def load_project_map_config(
    project_root: Path | None = None,
    *,
    map_dir: Path | None = None,
) -> ProjectMapConfig:
    resolved_map_dir = map_dir or find_map_dir(project_root)
    if resolved_map_dir is None:
        raise ValueError(
            f"No {MAP_DIR_NAME}/{CONFIG_FILE} found. Run `map bootstrap` or copy from {MAP_DIR_NAME}/config.yaml.example."
        )

    config = _read_yaml(resolved_map_dir / CONFIG_FILE)
    agents_meta = _read_yaml(resolved_map_dir / AGENTS_FILE)
    agents_local = _read_yaml(resolved_map_dir / AGENTS_LOCAL_FILE)

    api_url = str(
        config.get("api_url")
        or os.environ.get("MAP_API_URL")
        or "http://localhost:8000"
    ).rstrip("/")
    project_key = config.get("project_key")
    if not project_key:
        raise ValueError(f"{resolved_map_dir / CONFIG_FILE} must set project_key")

    default_persona = str(config.get("default_persona") or DEFAULT_PERSONA)
    raw_personas = agents_meta.get("personas") or {}
    personas: dict[str, PersonaInfo] = {}
    for key, value in raw_personas.items():
        if not isinstance(value, dict):
            continue
        agent_name = value.get("agent_name")
        if not agent_name:
            raise ValueError(f"personas.{key}.agent_name is required in agents.yaml")
        personas[key] = PersonaInfo(
            name=key,
            agent_name=str(agent_name),
            description=value.get("description"),
            role=value.get("role"),
        )

    raw_tokens = agents_local.get("personas") or {}
    tokens: dict[str, str] = {}
    for key, value in raw_tokens.items():
        if isinstance(value, dict) and value.get("token"):
            tokens[key] = str(value["token"])
        elif isinstance(value, str) and value:
            tokens[key] = value

    return ProjectMapConfig(
        map_dir=resolved_map_dir,
        api_url=api_url,
        project_key=str(project_key),
        project_id=str(config["project_id"]) if config.get("project_id") else None,
        default_persona=default_persona,
        personas=personas,
        tokens=tokens,
    )


def resolve_client(
    *,
    persona: str | None = None,
    project_root: Path | None = None,
    transport: Any = None,
) -> MAPClient:
    """Prefer project `.map/` persona; fall back to MAP_TOKEN / ~/.map/config.yaml."""
    map_dir = find_map_dir(project_root)
    if persona is not None or map_dir is not None:
        cfg = load_project_map_config(project_root=project_root, map_dir=map_dir)
        return cfg.client_for(persona, transport=transport)

    from map_client.config import load_config

    env_cfg = load_config()
    if not env_cfg.get("token"):
        raise ValueError(
            "No MAP credentials: add .map/agents.local.yaml (map bootstrap) or set MAP_TOKEN / ~/.map/config.yaml"
        )
    return MAPClient(env_cfg["api_url"], env_cfg["token"], transport=transport)
