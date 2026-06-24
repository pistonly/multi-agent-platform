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
def patched_admin_cli(monkeypatch, client, admin_headers):
    token = admin_headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))


def test_cli_project_status_revise(runner: CliRunner, patched_admin_cli, project, tmp_path):
    status_file = tmp_path / "status.md"
    status_file.write_text("# Current Status\n\n## 当前目标\n\n- CLI 更新\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "project",
            "status",
            "revise",
            "--project",
            project["id"],
            "--file",
            str(status_file),
            "--note",
            "from cli",
        ],
    )
    assert result.exit_code == 0, result.output
    body = yaml.safe_load(result.output)
    assert body["version"] == 2
    assert "CLI 更新" in body["content_md"]
