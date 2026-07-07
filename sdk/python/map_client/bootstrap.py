"""Bootstrap a code repo on MAP: project + persona agents + `.map/` files."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from map_types import AgentCreateResponse

from map_client.client import MAPClient
from map_client.exceptions import MAPHTTPError
from map_client.project_config import (
    AGENTS_FILE,
    AGENTS_LOCAL_FILE,
    CONFIG_FILE,
    MAP_DIR_NAME,
    PersonaInfo,
    ProjectMapConfig,
    _read_yaml,
)

DEFAULT_PERSONAS: dict[str, dict[str, str]] = {
    "host": {
        "agent_name_suffix": "host",
        "description": "主持话题、从话题开实验、推进实验生命周期",
        "role": "agent",
    },
    "participant": {
        "agent_name_suffix": "participant",
        "description": "参与话题讨论、提交 review、查看实验结果",
        "role": "agent",
    },
    "reviewer": {
        "agent_name_suffix": "reviewer",
        "description": "评审实验计划、写 reasonable/unreasonable 项",
        "role": "agent",
    },
}


@dataclass(frozen=True)
class BootstrapResult:
    config: ProjectMapConfig
    created_project: bool
    skipped_agent_names: list[str]


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "project"


def _admin_client(api_url: str, *, transport: Any = None) -> MAPClient:
    token = os.environ.get("MAP_ADMIN_TOKEN")
    admin_file = Path.home() / ".map" / "admin.yaml"
    if not token and admin_file.is_file():
        data = _read_yaml(admin_file)
        token = data.get("token") or data.get("api_token")
    if not token:
        raise ValueError(
            "Admin token required for bootstrap. Set MAP_ADMIN_TOKEN or ~/.map/admin.yaml with token: ..."
        )
    return MAPClient(api_url.rstrip("/"), str(token), transport=transport)


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _register_agent(
    client: MAPClient,
    *,
    name: str,
    role: str,
    project_key: str | None,
) -> AgentCreateResponse | None:
    try:
        if role == "admin":
            return client.register_agent(name, role="admin")
        return client.register_agent(name, role="agent", project_key=project_key)
    except MAPHTTPError as exc:
        if exc.status_code == 409:
            return None
        raise


def bootstrap_project_map(
    *,
    project_key: str,
    project_name: str,
    workspace_path: str | Path,
    project_root: Path | None = None,
    api_url: str | None = None,
    description: str | None = None,
    personas: dict[str, dict[str, str]] | None = None,
    force: bool = False,
    transport: Any = None,
) -> BootstrapResult:
    root = (project_root or Path.cwd()).resolve()
    map_dir = root / MAP_DIR_NAME
    local_path = map_dir / AGENTS_LOCAL_FILE

    if local_path.is_file() and not force:
        raise ValueError(
            f"{local_path} already exists. Remove it or pass --force "
            "(existing agent tokens cannot be recovered if re-registered)."
        )

    resolved_api_url = (api_url or os.environ.get("MAP_API_URL") or "http://localhost:8000").rstrip("/")
    admin = _admin_client(resolved_api_url, transport=transport)

    try:
        project = admin.create_project(
            project_key=project_key,
            name=project_name,
            workspace_path=str(workspace_path),
            description=description,
        )
        project_id = str(project.id)
        created_project = True
    except MAPHTTPError as exc:
        if exc.status_code == 409:
            project = admin.get_project_by_key(project_key)
            project_id = str(project.id)
            created_project = False
        else:
            raise

    persona_defs = personas or DEFAULT_PERSONAS
    slug = _slug(project_key)
    agents_yaml: dict[str, Any] = {"personas": {}}
    agents_local: dict[str, Any] = {"personas": {}}
    skipped: list[str] = []

    for persona_key, spec in persona_defs.items():
        suffix = spec.get("agent_name_suffix") or persona_key
        agent_name = spec.get("agent_name") or f"{slug}-{suffix}"
        role = spec.get("role") or "agent"
        agents_yaml["personas"][persona_key] = {
            "agent_name": agent_name,
            "description": spec.get("description"),
            "role": role,
        }
        created = _register_agent(
            admin,
            name=agent_name,
            role=role,
            project_key=project_key,
        )
        if created is None:
            skipped.append(agent_name)
            continue
        agents_local["personas"][persona_key] = {
            "token": created.api_token,
            "agent_id": str(created.id),
            "agent_name": agent_name,
        }

    config_yaml = {
        "api_url": resolved_api_url,
        "project_key": project_key,
        "project_id": project_id,
        "default_persona": "host",
    }

    _write_yaml(map_dir / CONFIG_FILE, config_yaml)
    _write_yaml(map_dir / AGENTS_FILE, agents_yaml)
    _write_yaml(local_path, agents_local)

    cfg = ProjectMapConfig(
        map_dir=map_dir,
        api_url=resolved_api_url,
        project_key=project_key,
        project_id=project_id,
        default_persona="host",
        personas={
            k: PersonaInfo(
                name=k,
                agent_name=v["agent_name"],
                description=v.get("description"),
                role=v.get("role"),
            )
            for k, v in agents_yaml["personas"].items()
        },
        tokens={k: v["token"] for k, v in agents_local["personas"].items()},
    )

    return BootstrapResult(config=cfg, created_project=created_project, skipped_agent_names=skipped)
