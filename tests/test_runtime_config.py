"""Offline configuration checks must match the SDK child without leaking secrets."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.agent_client import LLM_ENV_KEYS, PersonaAgentClient, apply_project_claude_env
from cli.commands.runtime import runtime_app


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    for key in LLM_ENV_KEYS | {"MAP_CLAUDE_ENV_FILE", "MAP_RUNTIME_EFFORT", "CLAUDE_CODE_EFFORT_LEVEL"}:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")


def make_agent(root, **kwargs):
    return PersonaAgentClient(persona="participant", state={}, save_state_fn=lambda: None,
                              project_root=root, **kwargs)


def write_env(root, content):
    path = root / ".map" / ".claude-env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_file_is_authoritative_without_changing_parent_env(tmp_path, monkeypatch):
    write_env(tmp_path, "export ANTHROPIC_AUTH_TOKEN=file-secret\nexport ANTHROPIC_MODEL=configured-model\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "inherited-secret")
    monkeypatch.setenv("ANTHROPIC_MODEL", "wrong-model")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "wrong-alias")
    home = Path.home()
    home.mkdir()
    (home / ".bashrc").write_text("export ANTHROPIC_BASE_URL=https://wrong.example\n")

    agent = make_agent(tmp_path)
    child = {**os.environ, **agent._resolve_env()}

    assert agent.model == "configured-model"
    assert child["ANTHROPIC_AUTH_TOKEN"] == "file-secret"
    assert not child["ANTHROPIC_API_KEY"]
    assert not child["ANTHROPIC_DEFAULT_SONNET_MODEL"]
    assert not child["ANTHROPIC_BASE_URL"]
    assert os.environ["ANTHROPIC_API_KEY"] == "inherited-secret"
    assert os.environ["ANTHROPIC_MODEL"] == "wrong-model"


def test_shared_file_selection_and_explicit_override(tmp_path, monkeypatch):
    write_env(tmp_path, "export ANTHROPIC_MODEL=local\n")
    shared = write_env(tmp_path / "shared", "export ANTHROPIC_MODEL=shared\n")
    explicit = write_env(tmp_path / "explicit", "export ANTHROPIC_MODEL=explicit\n")
    monkeypatch.setenv("MAP_CLAUDE_ENV_FILE", str(shared))
    assert make_agent(tmp_path).model == "shared"
    assert make_agent(tmp_path, env_file=explicit).model == "explicit"
    assert make_agent(tmp_path, model="cli-override").model == "cli-override"
    assert apply_project_claude_env(tmp_path)["ANTHROPIC_MODEL"] == "shared"


def test_invalid_explicit_file_does_not_fall_back(tmp_path, monkeypatch):
    write_env(tmp_path, "export ANTHROPIC_AUTH_TOKEN=working-local-token\n")
    monkeypatch.setenv("MAP_CLAUDE_ENV_FILE", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="does not exist"):
        make_agent(tmp_path)


def test_invalid_encoding_is_an_actionable_error(tmp_path):
    path = write_env(tmp_path, "")
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(ValueError, match="Cannot read Claude runtime env file"):
        make_agent(tmp_path)


def test_legacy_env_and_rc_fallback_without_file(tmp_path, monkeypatch):
    home = Path.home()
    home.mkdir()
    (home / ".bashrc").write_text("export ANTHROPIC_AUTH_TOKEN=rc-secret\nexport ANTHROPIC_MODEL=rc-model\n")
    monkeypatch.setenv("ANTHROPIC_MODEL", "shell-model")
    agent = make_agent(tmp_path)
    assert agent.model == "shell-model"
    assert agent._resolve_env()["ANTHROPIC_AUTH_TOKEN"] == "rc-secret"
    assert "ANTHROPIC_API_KEY" not in agent._resolve_env()


@pytest.mark.parametrize("file_key", ["MAP_RUNTIME_EFFORT", "CLAUDE_CODE_EFFORT_LEVEL"])
def test_effort_precedence(tmp_path, monkeypatch, file_key):
    assert make_agent(tmp_path).effort == "medium"
    write_env(tmp_path, f"export {file_key}=low\n")
    assert make_agent(tmp_path).effort == "low"
    monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "high")
    assert make_agent(tmp_path).effort == "high"
    monkeypatch.setenv("MAP_RUNTIME_EFFORT", "xhigh")
    assert make_agent(tmp_path).effort == "xhigh"
    agent = make_agent(tmp_path, effort="medium")
    assert agent.effort == "medium"
    assert agent._resolve_env()["CLAUDE_CODE_EFFORT_LEVEL"] == "medium"


def test_runtime_check_never_prints_credentials_or_endpoint(tmp_path, monkeypatch):
    shared = write_env(tmp_path / "shared", "export ANTHROPIC_AUTH_TOKEN=do-not-print-me\n"
                       "export ANTHROPIC_BASE_URL=https://user:password@example.test/private\n"
                       "export MAP_RUNTIME_EFFORT=low\n")
    monkeypatch.setattr("importlib.util.find_spec", lambda _: object())
    result = CliRunner().invoke(runtime_app, ["check", "--project-root", str(tmp_path),
                                             "--env-file", str(shared), "--persona", "participant"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["status"] == "configured"
    assert data["effort"] == "low"
    assert data["credential_keys"] == ["ANTHROPIC_AUTH_TOKEN"]
    assert data["gateway_verified"] is False
    assert "do-not-print-me" not in result.output
    assert "password" not in result.output
    assert "example.test" not in result.output
    assert not (tmp_path / ".map").exists()  # no state, runtime home or SDK session created


def test_runtime_check_missing_credentials_gives_next_step(tmp_path, monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda _: None)
    result = CliRunner().invoke(runtime_app, ["check", "--project-root", str(tmp_path)])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "needs_configuration"
    assert len(data["issues"]) == 2
    assert "MAP_CLAUDE_ENV_FILE" in result.stdout


def test_runtime_check_bad_file_returns_structured_error(tmp_path):
    result = CliRunner().invoke(runtime_app, ["check", "--project-root", str(tmp_path),
                                             "--env-file", str(tmp_path / "missing")])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "error"


def test_waker_invalid_shared_file_fails_before_startup(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from cli.simple_waker import APP

    monkeypatch.setenv("MAP_CLAUDE_ENV_FILE", str(tmp_path / "missing"))
    build = Mock()
    monkeypatch.setattr("cli.simple_waker.build_waker_client", build)
    result = CliRunner().invoke(APP, ["--persona", "participant", "--project-root", str(tmp_path),
                                      "--runtime", "claude", "--once", "--dry-run"])
    assert result.exit_code == 2
    assert "[simple-waker]" in result.stderr
    assert "does not exist" in result.stderr
    assert "Traceback" not in result.output
    build.assert_not_called()
