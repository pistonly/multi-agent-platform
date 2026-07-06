"""CLI tests for `map mention dismiss` / `map mention dismiss-all`."""

from __future__ import annotations

import yaml
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app
from map_client import project_config
from map_client.testing import MAPTestClientTransport

import pytest

pytestmark = pytest.mark.slow


def _create_mention_for_reviewer(client, auth_headers, reviewer_headers, project) -> str:
    exp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "Mention CLI", "plan": {"content_md": "# p"}},
    ).json()
    plan = client.get(f"/api/v1/experiments/{exp['id']}/plans/1", headers=auth_headers).json()
    client.post(
        f"/api/v1/experiments/{exp['id']}/comments",
        headers=auth_headers,
        json={
            "anchor_type": "plan",
            "anchor_id": plan["id"],
            "body": "@reviewer-agent please look",
        },
    )
    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert len(todos["mentions"]) == 1
    return todos["mentions"][0]["id"]


def test_mention_dismiss_cli(client, auth_headers, reviewer, project, monkeypatch):
    reviewer_headers = reviewer["headers"]
    token = reviewer_headers["Authorization"].removeprefix("Bearer ")
    mention_id = _create_mention_for_reviewer(client, auth_headers, reviewer_headers, project)

    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))
    monkeypatch.setattr(cli_main, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(project_config, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "map_client.config.load_config",
        lambda *args, **kwargs: {"api_url": "http://test", "token": token, "project_key": None},
    )

    result = CliRunner().invoke(app, ["mention", "dismiss", "--id", mention_id])
    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.stdout)
    assert payload["id"] == mention_id
    assert payload["dismissed_at"] is not None

    todos = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert todos["mentions"] == []


def test_mention_dismiss_all_cli(client, auth_headers, reviewer, project, monkeypatch):
    reviewer_headers = reviewer["headers"]
    token = reviewer_headers["Authorization"].removeprefix("Bearer ")
    _create_mention_for_reviewer(client, auth_headers, reviewer_headers, project)
    exp2 = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "Mention CLI 2", "plan": {"content_md": "# p2"}},
    ).json()
    plan2 = client.get(f"/api/v1/experiments/{exp2['id']}/plans/1", headers=auth_headers).json()
    client.post(
        f"/api/v1/experiments/{exp2['id']}/comments",
        headers=auth_headers,
        json={
            "anchor_type": "plan",
            "anchor_id": plan2["id"],
            "body": "@reviewer-agent second",
        },
    )
    todos_before = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert len(todos_before["mentions"]) == 2

    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))
    monkeypatch.setattr(cli_main, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(project_config, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "map_client.config.load_config",
        lambda *args, **kwargs: {"api_url": "http://test", "token": token, "project_key": None},
    )

    result = CliRunner().invoke(app, ["mention", "dismiss-all"])
    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.stdout)
    assert payload["dismissed"] == 2

    todos_after = client.get("/api/v1/agents/me/todos", headers=reviewer_headers).json()
    assert todos_after["mentions"] == []
