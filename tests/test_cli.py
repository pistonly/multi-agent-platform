from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app
from map_client.testing import MAPTestClientTransport


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched_cli(monkeypatch, client, auth_headers):
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))


@pytest.fixture
def patched_admin_cli(monkeypatch, client, admin_headers):
    token = admin_headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))


def test_cli_admin_create_project(runner, patched_admin_cli):
    result = runner.invoke(
        app,
        ["project", "create", "--key", "cli-project", "--name", "CLI项目", "--path", "/tmp/cli"],
    )
    assert result.exit_code == 0, result.output
    project = yaml.safe_load(result.output)
    assert project["project_key"] == "cli-project"


def test_cli_experiment_flow(runner, patched_cli, project, tmp_path: Path):
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("## CLI plan", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "experiment",
            "create",
            "--title",
            "CLI实验",
            "--plan-file",
            str(plan_file),
            "--submit-for-review",
        ],
    )
    assert result.exit_code == 0, result.output
    experiment = yaml.safe_load(result.output)
    assert experiment["phase"] == "review"

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    status = yaml.safe_load(result.output)
    assert status["experiment_counts_by_phase"]["review"] >= 1

    result = runner.invoke(app, ["experiment", "status", "--id", experiment["id"]])
    assert result.exit_code == 0, result.output
