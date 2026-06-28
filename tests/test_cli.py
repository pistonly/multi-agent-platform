from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app
from map_client import project_config
from map_client.testing import MAPTestClientTransport


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched_cli(monkeypatch, client, auth_headers):
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.delenv("MAP_PROJECT_KEY", raising=False)
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))
    monkeypatch.setattr(cli_main, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(project_config, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "map_client.config.load_config",
        lambda *args, **kwargs: {"api_url": "http://test", "token": token, "project_key": None},
    )


@pytest.fixture
def patched_admin_cli(monkeypatch, client, admin_headers):
    token = admin_headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.delenv("MAP_PROJECT_KEY", raising=False)
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))
    monkeypatch.setattr(cli_main, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(project_config, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "map_client.config.load_config",
        lambda *args, **kwargs: {"api_url": "http://test", "token": token, "project_key": None},
    )


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

    result = runner.invoke(app, ["experiment", "list", "--phase", "review", "--q", "CLI实验", "--page-size", "1"])
    assert result.exit_code == 0, result.output
    experiments = yaml.safe_load(result.output)
    assert len(experiments) == 1
    assert experiments[0]["id"] == experiment["id"]


def test_cli_topic_flow(runner, patched_cli, project):
    result = runner.invoke(app, ["topic", "create", "--title", "CLI话题", "--description", "desc"])
    assert result.exit_code == 0, result.output
    topic = yaml.safe_load(result.output)
    assert topic["status"] == "open"
    topic_id = topic["id"]

    result = runner.invoke(app, ["topic", "comment", "--id", topic_id, "--body", "评论"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["topic", "show", "--id", topic_id])
    assert result.exit_code == 0, result.output
    detail = yaml.safe_load(result.output)
    assert detail["comment_count"] == 1
    assert detail["discussion_round"] == "round1"

    result = runner.invoke(app, ["topic", "advance-round", "--id", topic_id])
    assert result.exit_code == 0, result.output
    advanced = yaml.safe_load(result.output)
    assert advanced["discussion_round"] == "round2"
    assert advanced["round_summary_count"] == 1

    result = runner.invoke(app, ["topic", "list", "--status", "open", "--q", "CLI话题", "--page-size", "1"])
    assert result.exit_code == 0, result.output
    topics = yaml.safe_load(result.output)
    assert len(topics) == 1
    assert topics[0]["id"] == topic_id

    result = runner.invoke(app, ["topic", "close", "--id", topic_id])
    assert result.exit_code == 0, result.output
    assert yaml.safe_load(result.output)["status"] == "closed"


def test_cli_persona_list_missing_map_dir(runner, monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MAP_TOKEN", raising=False)
    result = runner.invoke(app, ["persona", "list"])
    assert result.exit_code == 1, result.output
    assert "map bootstrap" in result.output
    assert "config.yaml" in result.output
