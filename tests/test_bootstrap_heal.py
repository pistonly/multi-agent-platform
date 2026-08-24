"""Unit tests for ``map bootstrap --heal`` (3b7c2b44 A2).

机器断言核心（plan A2）：heal 前后 ``agents.local.yaml`` 字节不变 + server
端 token 不变（不 create / 不 reissue —— 用 MockTransport 记录请求方法，只
允许 GET 授权查询，出现任一 POST 即失败）。
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml
from map_client.bootstrap import heal_project_map_config
from map_client.project_config import AGENTS_FILE, AGENTS_LOCAL_FILE, CONFIG_FILE

AUTHORITY_ID = "11111111-1111-1111-1111-111111111111"
PROJECT_BODY = {
    "id": AUTHORITY_ID,
    "project_key": "demo-key",
    "name": "Demo",
    "workspace_path": "/tmp/demo",
    "content_root": "map",
    "fs_freshness_sla_seconds": None,
    "description": None,
    "current_status_version": 0,
    "created_at": "2026-08-25T00:00:00",
    "archived_at": None,
}


def _agents_body(*names: str) -> list[dict]:
    return [
        {
            "id": f"aa000000-0000-4000-8000-{i:012d}",
            "name": n,
            "role": "agent",
            "project_id": AUTHORITY_ID,
            "project_key": "demo-key",
            "created_at": "2026-08-25T00:00:00",
        }
        for i, n in enumerate(names)
    ]


def _write_map(
    root: Path,
    *,
    config: dict,
    agents: dict | None = None,
    local: dict | None = None,
) -> None:
    map_dir = root / ".map"
    map_dir.mkdir(parents=True, exist_ok=True)
    (map_dir / CONFIG_FILE).write_text(yaml.safe_dump(config), encoding="utf-8")
    if agents is not None:
        (map_dir / AGENTS_FILE).write_text(yaml.safe_dump(agents), encoding="utf-8")
    if local is not None:
        (map_dir / AGENTS_LOCAL_FILE).write_text(yaml.safe_dump(local), encoding="utf-8")


def _transport(project_body: dict | None = None, agents: list[dict] | None = None) -> tuple[httpx.MockTransport, list[str]]:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        path = request.url.path
        if "/projects/by-key/" in path:
            if project_body is None:
                return httpx.Response(404, json={"detail": "no such project"})
            return httpx.Response(200, json=project_body)
        if path.endswith("/agents"):
            return httpx.Response(200, json=agents or [])
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.MockTransport(handler), methods


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_heal_rewrites_stale_project_id_preserving_local_token(tmp_path: Path) -> None:
    stale = "22222222-2222-2222-2222-222222222222"
    local_before = {
        "personas": {
            "host": {"token": "keep-me-token", "agent_id": "a1", "agent_name": "demo-key-host"}
        }
    }
    _write_map(
        tmp_path,
        config={"project_key": "demo-key", "api_url": "http://localhost:18400", "project_id": stale},
        agents={"personas": {"host": {"agent_name": "demo-key-host", "role": "agent"}}},
        local=local_before,
    )
    local_path = tmp_path / ".map" / AGENTS_LOCAL_FILE
    local_bytes_before = local_path.read_bytes()

    mock, methods = _transport(project_body=PROJECT_BODY, agents=_agents_body("demo-key-host"))
    result = heal_project_map_config(project_root=tmp_path, transport=mock)

    assert result.config_rewritten is True
    assert _load_yaml(tmp_path / ".map" / CONFIG_FILE)["project_id"] == AUTHORITY_ID
    # 机器断言 1：agents.local.yaml 一字未变
    assert local_path.read_bytes() == local_bytes_before
    # 机器断言 2：server 侧零写请求（无 create / 无 reissue）
    assert methods == ["GET", "GET"]
    assert all(m == "GET" for m in methods)
    assert result.agents_local_untouched is True


def test_heal_fixes_stale_agent_name_by_convention(tmp_path: Path) -> None:
    _write_map(
        tmp_path,
        config={"project_key": "demo-key", "api_url": "http://localhost:18400", "project_id": AUTHORITY_ID},
        agents={"personas": {"host": {"agent_name": "wrong-old-name", "role": "agent"}}},
        local={"personas": {"host": {"token": "keep", "agent_id": "a1", "agent_name": "wrong-old-name"}}},
    )
    local_path = tmp_path / ".map" / AGENTS_LOCAL_FILE
    local_bytes_before = local_path.read_bytes()

    mock, methods = _transport(project_body=PROJECT_BODY, agents=_agents_body("demo-key-host"))
    result = heal_project_map_config(project_root=tmp_path, transport=mock)

    assert result.agents_rewritten is True
    assert _load_yaml(tmp_path / ".map" / AGENTS_FILE)["personas"]["host"]["agent_name"] == "demo-key-host"
    assert result.fixed_agent_names == [("host", "wrong-old-name", "demo-key-host")]
    assert local_path.read_bytes() == local_bytes_before
    assert methods == ["GET", "GET"]


def test_heal_clean_leaves_files_untouched(tmp_path: Path) -> None:
    config_before = {
        "project_key": "demo-key",
        "api_url": "http://localhost:18400",
        "project_id": AUTHORITY_ID,
    }
    agents_before = {"personas": {"host": {"agent_name": "demo-key-host", "role": "agent"}}}
    local_before = {"personas": {"host": {"token": "keep", "agent_id": "a1", "agent_name": "demo-key-host"}}}
    _write_map(tmp_path, config=config_before, agents=agents_before, local=local_before)

    mock, methods = _transport(project_body=PROJECT_BODY, agents=_agents_body("demo-key-host"))
    result = heal_project_map_config(project_root=tmp_path, transport=mock)

    assert result.config_rewritten is False
    assert result.agents_rewritten is False
    assert result.fixed_agent_names is None
    assert _load_yaml(tmp_path / ".map" / CONFIG_FILE) == config_before
    assert _load_yaml(tmp_path / ".map" / AGENTS_FILE) == agents_before
    assert methods == ["GET", "GET"]


def test_heal_without_any_token_raises(tmp_path: Path) -> None:
    _write_map(
        tmp_path,
        config={"project_key": "demo-key", "api_url": "http://localhost:18400", "project_id": "22222222-2222-2222-2222-222222222222"},
        agents={"personas": {"host": {"agent_name": "demo-key-host", "role": "agent"}}},
    )
    with pytest.raises(ValueError, match="reissue"):
        heal_project_map_config(project_root=tmp_path, transport=httpx.MockTransport(lambda r: httpx.Response(404)))


def test_heal_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        heal_project_map_config(project_root=tmp_path, transport=httpx.MockTransport(lambda r: httpx.Response(404)))


def test_heal_key_not_registered_404_raises(tmp_path: Path) -> None:
    _write_map(
        tmp_path,
        config={"project_key": "demo-key", "api_url": "http://localhost:18400", "project_id": "22222222-2222-2222-2222-222222222222"},
        agents={"personas": {"host": {"agent_name": "demo-key-host", "role": "agent"}}},
        local={"personas": {"host": {"token": "keep", "agent_id": "a1", "agent_name": "demo-key-host"}}},
    )
    with pytest.raises(ValueError, match="未在服务端注册"):
        heal_project_map_config(project_root=tmp_path, transport=httpx.MockTransport(lambda r: httpx.Response(404, json={"detail": "no"})))
