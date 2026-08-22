"""Bootstrap a code repo on MAP: project + persona agents + `.map/` files."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml
from map_types import AgentCreateResponse, BootstrapResponse, TokenReissueResponse

from map_client.client import MAPClient, _is_local_url
from map_client.exceptions import MAPHTTPError, raise_for_status
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


@dataclass(frozen=True)
class ReissueResult:
    """Outcome of ``map auth reissue`` (M52C)."""

    persona_key: str | None
    agent_name: str
    agent_id: str
    api_url: str
    local_path: Path
    # True when the previous token (if any) was unknown to the caller —
    # the server revoked it either way.
    wrote_back: bool = True


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "project"


def admin_client(api_url: str, *, transport: Any = None) -> MAPClient:
    token = os.environ.get("MAP_ADMIN_TOKEN")
    admin_file = Path.home() / ".map" / "admin.yaml"
    if not token and admin_file.is_file():
        data = _read_yaml(admin_file)
        token = data.get("token") or data.get("api_token")
    if not token:
        raise ValueError(
            "Admin token required. Set MAP_ADMIN_TOKEN or create ~/.map/admin.yaml with token: <value>"
        )
    return MAPClient(api_url.rstrip("/"), str(token), transport=transport)


def _public_bootstrap(
    api_url: str,
    *,
    project_key: str,
    project_name: str,
    workspace_path: str,
    description: str | None,
    content_root: str = "map",
    transport: Any = None,
) -> BootstrapResponse | None:
    """Try the self-service ``POST /api/v1/bootstrap`` endpoint (no admin token).

    Returns the parsed ``BootstrapResponse`` on success. Returns ``None``
    if the endpoint doesn't exist (old server without the self-service
    path) so the caller can fall back to the admin-token flow. Other
    errors (409 conflict, 422 validation, 5xx) raise ``MAPHTTPError``.
    """
    url = f"{api_url.rstrip('/')}/api/v1/bootstrap"
    body: dict[str, Any] = {
        "project_key": project_key,
        "project_name": project_name,
        "workspace_path": workspace_path,
        "content_root": content_root,
    }
    if description is not None:
        body["description"] = description
    try:
        # 与 MAPClient 同策略：本地地址忽略环境代理，避免 SOCKS 代理
        # 初始化失败等影响首次 bootstrap / reissue（FTUE 关键路径）。
        client = httpx.Client(
            transport=transport, timeout=30.0, trust_env=not _is_local_url(url)
        )
    except Exception:
        return None
    try:
        resp = client.post(url, json=body)
    except Exception:
        return None
    finally:
        client.close()
    if resp.status_code == 404:
        # Old server — no /bootstrap endpoint; fall back to admin path.
        return None
    if resp.status_code >= 400:
        detail = resp.text
        error_code: str | None = None
        hint: str | None = None
        retryable: bool | None = None
        if resp.content:
            try:
                payload = resp.json()
                if isinstance(payload, dict) and "detail" in payload:
                    detail = str(payload["detail"])
                if isinstance(payload, dict):
                    error_code = payload.get("error_code")
                    hint = payload.get("hint")
                    retryable = payload.get("retryable")
            except Exception:
                pass
        raise_for_status(
            resp.status_code,
            detail,
            error_code=error_code if isinstance(error_code, str) else None,
            hint=hint if isinstance(hint, str) else None,
            retryable=retryable if isinstance(retryable, bool) else None,
        )
    return BootstrapResponse.model_validate(resp.json())


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _public_reissue(
    api_url: str,
    *,
    project_key: str,
    agent_name: str,
    transport: Any = None,
) -> TokenReissueResponse | None:
    """Call the self-service ``POST /api/v1/bootstrap/reissue`` endpoint.

    Returns the parsed ``TokenReissueResponse`` on success, or ``None``
    when the endpoint is missing (server < v0.11). Other errors (404
    unknown project/agent, 422, 5xx) raise ``MAPHTTPError``.
    """
    url = f"{api_url.rstrip('/')}/api/v1/bootstrap/reissue"
    try:
        # 同上：本地地址忽略环境代理。
        client = httpx.Client(
            transport=transport, timeout=30.0, trust_env=not _is_local_url(url)
        )
    except Exception:
        return None
    try:
        resp = client.post(
            url, json={"project_key": project_key, "agent_name": agent_name}
        )
    except Exception:
        return None
    finally:
        client.close()
    if resp.status_code == 404:
        # Unknown project/agent is also 404 — distinguish by error body.
        # A missing route returns FastAPI's default {"detail": "Not Found"}
        # (exact literal); a real NotFoundError carries a richer domain
        # message (e.g. "project_key 'x' not found. ...").
        detail = ""
        try:
            payload = resp.json()
            detail = str(payload.get("detail", "")) if isinstance(payload, dict) else ""
        except Exception:
            pass
        if detail.strip().lower() in ("", "not found"):
            return None
    if resp.status_code >= 400:
        detail = resp.text
        error_code: str | None = None
        hint: str | None = None
        retryable: bool | None = None
        if resp.content:
            try:
                payload = resp.json()
                if isinstance(payload, dict) and "detail" in payload:
                    detail = str(payload["detail"])
                if isinstance(payload, dict):
                    error_code = payload.get("error_code")
                    hint = payload.get("hint")
                    retryable = payload.get("retryable")
            except Exception:
                pass
        raise_for_status(
            resp.status_code,
            detail,
            error_code=error_code if isinstance(error_code, str) else None,
            hint=hint if isinstance(hint, str) else None,
            retryable=retryable if isinstance(retryable, bool) else None,
        )
    return TokenReissueResponse.model_validate(resp.json())


def reissue_map_token(
    *,
    agent_name: str,
    project_key: str | None = None,
    project_root: Path | None = None,
    api_url: str | None = None,
    transport: Any = None,
) -> ReissueResult:
    """Reissue one agent token and write it back to ``.map/agents.local.yaml``.

    Resolution order for the arguments: explicit args → ``.map/config.yaml``
    → ``MAP_API_URL``. Raises ``ValueError`` when ``.map/`` is not
    initialized (bootstrap first) or the server predates the reissue
    endpoint.
    """
    root = (project_root or Path.cwd()).resolve()
    map_dir = root / MAP_DIR_NAME
    config_path = map_dir / CONFIG_FILE
    local_path = map_dir / AGENTS_LOCAL_FILE

    config: dict[str, Any] = {}
    if config_path.is_file():
        config = _read_yaml(config_path) or {}

    resolved_key = project_key or config.get("project_key")
    if not resolved_key:
        raise ValueError(
            f"project_key not found: pass --key or initialize {config_path} "
            "with `map bootstrap` first."
        )
    resolved_api_url = (
        (api_url or config.get("api_url") or os.environ.get("MAP_API_URL") or "http://localhost:18400").rstrip("/")
    )

    # Locate the persona (if any) that maps to this agent_name — for the
    # write-back key. Custom agents without a persona entry still get a
    # local token entry keyed by the agent name.
    persona_key: str | None = None
    agents_path = map_dir / AGENTS_FILE
    if agents_path.is_file():
        agents_data = _read_yaml(agents_path) or {}
        for key, spec in (agents_data.get("personas") or {}).items():
            if isinstance(spec, dict) and spec.get("agent_name") == agent_name:
                persona_key = str(key)
                break

    resp = _public_reissue(
        resolved_api_url,
        project_key=resolved_key,
        agent_name=agent_name,
        transport=transport,
    )
    if resp is None:
        raise ValueError(
            "server does not support token reissue (needs MAP server >= v0.11). "
            "Ask the admin to rotate the token, or bootstrap with a new project_key."
        )

    local: dict[str, Any] = {"personas": {}}
    if local_path.is_file():
        loaded = _read_yaml(local_path)
        if isinstance(loaded, dict) and isinstance(loaded.get("personas"), dict):
            local = loaded
    write_key = persona_key or agent_name
    local.setdefault("personas", {})[write_key] = {
        "token": resp.api_token,
        "agent_id": str(resp.agent_id),
        "agent_name": resp.agent_name,
    }
    _write_yaml(local_path, local)

    return ReissueResult(
        persona_key=persona_key,
        agent_name=resp.agent_name,
        agent_id=str(resp.agent_id),
        api_url=resolved_api_url,
        local_path=local_path,
    )


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


def _finalize_from_public_bootstrap(
    resp: BootstrapResponse,
    *,
    map_dir: Path,
    api_url: str,
    project_key: str,
    personas: dict[str, dict[str, str]],
) -> BootstrapResult:
    """Convert a successful ``POST /bootstrap`` response into ``.map/`` files.

    The server already created the project + 3 persona agents atomically;
    here we just write the 3 YAML files and build the ``ProjectMapConfig``
    so the return value matches the admin-token path.
    """
    project_id = str(resp.project.id)
    agents_yaml: dict[str, Any] = {"personas": {}}
    agents_local: dict[str, Any] = {"personas": {}}

    for agent_info in resp.agents:
        persona_key = agent_info.persona
        spec = personas.get(persona_key, {})
        agents_yaml["personas"][persona_key] = {
            "agent_name": agent_info.agent_name,
            "description": spec.get("description"),
            "role": "agent",
        }
        agents_local["personas"][persona_key] = {
            "token": agent_info.api_token,
            "agent_id": str(agent_info.agent_id),
            "agent_name": agent_info.agent_name,
        }

    config_yaml = {
        "api_url": api_url,
        "project_key": project_key,
        "project_id": project_id,
        "default_persona": "host",
        "content_root": resp.project.content_root or "map",
    }
    _write_yaml(map_dir / CONFIG_FILE, config_yaml)
    _write_yaml(map_dir / AGENTS_FILE, agents_yaml)
    _write_yaml(map_dir / AGENTS_LOCAL_FILE, agents_local)

    cfg = ProjectMapConfig(
        map_dir=map_dir,
        api_url=api_url,
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
    return BootstrapResult(config=cfg, created_project=True, skipped_agent_names=[])


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
            "(re-registering does not recover existing tokens; use "
            f"`map auth reissue --key {project_key} --name <agent-name>` "
            "to recover them)."
        )

    resolved_api_url = (api_url or os.environ.get("MAP_API_URL") or "http://localhost:18400").rstrip("/")

    # 优先尝试自助 bootstrap 端点（无需 admin token，新版本 server 支持）
    public_resp = _public_bootstrap(
        resolved_api_url,
        project_key=project_key,
        project_name=project_name,
        workspace_path=str(workspace_path),
        description=description,
        content_root="map",
        transport=transport,
    )
    if public_resp is not None:
        return _finalize_from_public_bootstrap(
            public_resp,
            map_dir=map_dir,
            api_url=resolved_api_url,
            project_key=project_key,
            personas=personas or DEFAULT_PERSONAS,
        )

    # 回退：admin token 路径（老版本 server 无 /bootstrap 端点）
    admin = admin_client(resolved_api_url, transport=transport)

    try:
        project = admin.create_project(
            project_key=project_key,
            name=project_name,
            workspace_path=str(workspace_path),
            description=description,
            content_root="map",
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
        "content_root": "map",
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
