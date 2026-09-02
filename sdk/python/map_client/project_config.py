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

BOOTSTRAP_HINT = (
    "Run:\n"
    "  map bootstrap --key <project-key> --name \"<Project Name>\" --api-url http://localhost:18400\n"
    f"Or copy from {MAP_DIR_NAME}/config.yaml.example."
)

LOCAL_PLANE_HINT = (
    "plane: local — this command requires a MAP server, but the project is "
    "configured as an offline local-plane project. Offline-supported commands: "
    "map bootstrap --local / map topic init, create, comment, advance-round, "
    "close, list, show, work, anomalies, archive, archive-index, doctor config. "
    "Do not run map sync or server-bound commands in a local-plane project."
)


def missing_map_config_message() -> str:
    return f"No {MAP_DIR_NAME}/{CONFIG_FILE} found. {BOOTSTRAP_HINT}"


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
    # 平面模式：``local`` = 离线本地平面（零注册/零 token/零 server，见
    # map bootstrap --local）；缺省 ``remote`` = 现状行为。
    plane: str = "remote"
    # 内容根名（``config.yaml`` ``content_root``，默认 ``map``）。此前由
    # cli.commands.fs._content_root_name 裸读 yaml；实验 e7244a91（A1）把
    # 它收进模型，供 ProjectContext.content_root 单点使用。文件格式不变。
    content_root: str | None = None

    def resolve_persona(self, persona: str) -> str:
        """归一到 personas 短名 key：已接受短名，也接受 agent_name 长名。

        `--persona` 参数的 key 是 personas 短名（host/participant/reviewer），
        但文档与 whoami 展示的是 agent_name 长名（multi-agents-platform-*）。允许
        长名反查短名，让两边写哪个都可用，避免按文档用长名时误撞 token 缺失错。
        """
        if persona in self.personas:
            return persona
        for key, info in self.personas.items():
            if info.agent_name == persona:
                return key
        return persona

    def token_for(self, persona: str) -> str:
        persona = self.resolve_persona(persona)
        if persona not in self.tokens:
            known = ", ".join(sorted(self.tokens)) or "(none)"
            raise ValueError(
                f"Unknown or missing token for persona '{persona}'. "
                f"--persona accepts a personas short name or an agent_name. Known: {known}"
            )
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
        raise ValueError(missing_map_config_message())

    config = _read_yaml(resolved_map_dir / CONFIG_FILE)
    agents_meta = _read_yaml(resolved_map_dir / AGENTS_FILE)
    agents_local = _read_yaml(resolved_map_dir / AGENTS_LOCAL_FILE)

    api_url = str(
        config.get("api_url")
        or os.environ.get("MAP_API_URL")
        or "http://localhost:18400"
    ).rstrip("/")
    project_key = config.get("project_key")
    if not project_key:
        raise ValueError(f"{resolved_map_dir / CONFIG_FILE} must set project_key")

    default_persona = str(config.get("default_persona") or DEFAULT_PERSONA)
    plane = str(config.get("plane") or "remote")
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
        plane=plane,
        content_root=str(config["content_root"]) if config.get("content_root") else None,
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
        if cfg.plane == "local":
            # 单一收口点：所有走 server 的 CLI 命令都经此建客户端；local
            # 平面项目在这里统一得到清晰提示（离线支持的命令不建客户端）。
            raise ValueError(LOCAL_PLANE_HINT)
        return cfg.client_for(persona, transport=transport)

    from map_client.config import load_config

    env_cfg = load_config()
    if not env_cfg.get("token"):
        raise ValueError(
            f"No MAP credentials: add .map/agents.local.yaml ({BOOTSTRAP_HINT.strip()}) "
            "or set MAP_TOKEN / ~/.map/config.yaml"
        )
    return MAPClient(env_cfg["api_url"], env_cfg["token"], transport=transport)
