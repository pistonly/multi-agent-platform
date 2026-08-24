"""Unit tests for M52C — ``map auth reissue`` (SDK write-back path).

Uses ``httpx.MockTransport`` so no real server is needed; verifies the
token rotation round-trip and the ``.map/agents.local.yaml`` write-back
conventions.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml
from map_client.bootstrap import HealResult, ReissueResult, reissue_map_token
from map_client.exceptions import MAPHTTPError
from typer.testing import CliRunner

from cli.commands.auth import auth_app

FAKE_REISSUE = ReissueResult(
    persona_key="host",
    agent_name="demo-host",
    agent_id="11111111-1111-1111-1111-111111111111",
    api_url="http://localhost:18400",
    local_path=Path("/tmp/x/.map/agents.local.yaml"),
)


def _init_map_dir(root: Path, *, agent_names: dict[str, str] | None = None) -> None:
    """Write a minimal .map/ layout (config + personas)."""
    map_dir = root / ".map"
    map_dir.mkdir(parents=True)
    (map_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "project_key": "demo-key",
                "api_url": "http://localhost:8001",
                "personas": {"host": {"agent_name": "demo-host"}},
            }
        ),
        encoding="utf-8",
    )
    personas = agent_names or {"host": "demo-host", "participant": "demo-participant"}
    (map_dir / "agents.yaml").write_text(
        yaml.safe_dump(
            {
                "personas": {
                    key: {"agent_name": name} for key, name in personas.items()
                }
            }
        ),
        encoding="utf-8",
    )


def _ok_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/api/v1/bootstrap/reissue"
    body = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "agent_id": "11111111-1111-1111-1111-111111111111",
            "agent_name": body["agent_name"],
            "project_key": body["project_key"],
            "api_token": "new-token-xyz",
            "previous_token_revoked": True,
            "reissued_at": "2026-01-01T00:00:00Z",
        },
    )


def test_reissue_writes_back_persona_token(tmp_path: Path) -> None:
    _init_map_dir(tmp_path)
    # An existing local file with another persona's token that must survive.
    (tmp_path / ".map" / "agents.local.yaml").write_text(
        yaml.safe_dump(
            {
                "personas": {
                    "participant": {"token": "keep-me", "agent_id": "p-1"},
                }
            }
        ),
        encoding="utf-8",
    )

    result = reissue_map_token(
        agent_name="demo-host",
        project_root=tmp_path,
        transport=httpx.MockTransport(_ok_handler),
    )

    assert result.persona_key == "host"
    assert result.agent_id == "11111111-1111-1111-1111-111111111111"

    local = yaml.safe_load(
        (tmp_path / ".map" / "agents.local.yaml").read_text(encoding="utf-8")
    )
    assert local["personas"]["host"]["token"] == "new-token-xyz"
    assert local["personas"]["host"]["agent_name"] == "demo-host"
    # Other personas untouched.
    assert local["personas"]["participant"]["token"] == "keep-me"


def test_reissue_without_map_dir_requires_key(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="project_key not found"):
        reissue_map_token(
            agent_name="demo-host",
            project_root=tmp_path,
            transport=httpx.MockTransport(_ok_handler),
        )


def test_reissue_old_server_returns_friendly_error(tmp_path: Path) -> None:
    """Server without the endpoint (bare 404) → actionable ValueError."""
    _init_map_dir(tmp_path)

    def not_found(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not Found"})

    with pytest.raises(ValueError, match="does not support token reissue"):
        reissue_map_token(
            agent_name="demo-host",
            project_root=tmp_path,
            transport=httpx.MockTransport(not_found),
        )


def test_reissue_unknown_agent_raises_http_error(tmp_path: Path) -> None:
    _init_map_dir(tmp_path)

    def unknown(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, json={"detail": "agent 'ghost' not found in project 'demo-key'."}
        )

    with pytest.raises(MAPHTTPError) as excinfo:
        reissue_map_token(
            agent_name="ghost",
            project_root=tmp_path,
            transport=httpx.MockTransport(unknown),
        )
    assert excinfo.value.status_code == 404


def test_reissue_custom_agent_without_persona_entry(tmp_path: Path) -> None:
    """Agent not mapped in agents.yaml still gets a local entry keyed by name."""
    _init_map_dir(tmp_path)

    result = reissue_map_token(
        agent_name="custom-bot",
        project_root=tmp_path,
        transport=httpx.MockTransport(_ok_handler),
    )
    assert result.persona_key is None

    local = yaml.safe_load(
        (tmp_path / ".map" / "agents.local.yaml").read_text(encoding="utf-8")
    )
    assert local["personas"]["custom-bot"]["token"] == "new-token-xyz"


# --- 3b7c2b44 A4：--rewrite-config 边界（默认不修 config，显式才一并修） ---


def test_reissue_cli_default_keeps_config_untouched(tmp_path: Path, monkeypatch) -> None:
    """默认 reissue 只写 token，不碰 config.yaml（token 丢失恢复语义回归）。"""
    _init_map_dir(tmp_path)
    config_path = tmp_path / ".map" / "config.yaml"
    before = config_path.read_bytes()
    heal_calls: list[dict] = []

    monkeypatch.setattr(
        "cli.commands.auth.reissue_map_token",
        lambda **kw: FAKE_REISSUE,
    )
    monkeypatch.setattr(
        "map_client.bootstrap.heal_project_map_config",
        lambda **kw: heal_calls.append(kw),
    )

    # auth_app 单命令组在 root 展平：不写 "reissue" token
    result = CliRunner().invoke(
        auth_app, ["--name", "demo-host", "--project-root", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert heal_calls == []  # 默认绝不触发 heal / 不修 config
    assert config_path.read_bytes() == before


def test_reissue_cli_rewrite_config_calls_heal_once(tmp_path: Path, monkeypatch) -> None:
    """仅显式 --rewrite-config 时顺带修 config；heal 恰好一次。"""
    _init_map_dir(tmp_path)
    heal_calls: list[dict] = []

    monkeypatch.setattr(
        "cli.commands.auth.reissue_map_token",
        lambda **kw: FAKE_REISSUE,
    )

    def fake_heal(**kw) -> HealResult:
        heal_calls.append(kw)
        return HealResult(
            project_key="demo-key",
            project_id="11111111-1111-1111-1111-111111111111",
            api_url="http://localhost:18400",
            map_dir=tmp_path / ".map",
            config_rewritten=True,
            agents_rewritten=False,
        )

    monkeypatch.setattr("map_client.bootstrap.heal_project_map_config", fake_heal)

    result = CliRunner().invoke(
        auth_app,
        [
            "--name", "demo-host",
            "--project-root", str(tmp_path),
            "--rewrite-config",
        ],
    )
    assert result.exit_code == 0
    assert len(heal_calls) == 1
    assert "config.yaml project_id 已回写" in result.output
