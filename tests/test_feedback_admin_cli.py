"""CLI ``map feedback list|get|update`` over the admin-token path.

These commands authenticate with the admin token (``MAP_ADMIN_TOKEN`` /
``~/.map/admin.yaml``) rather than a project persona token, because the
server-side handlers require the admin role. Tests drive the registered
Typer commands via ``CliRunner`` + ``MAPTestClientTransport`` — no live
server — mirroring ``test_audit_admin_cli.py``.

``map feedback submit`` stays on the persona path (no admin gate); a
regression test pins that so it cannot be accidentally flipped to admin.
"""

from __future__ import annotations

import pytest
import yaml
from map_client.testing import MAPTestClientTransport
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app


def _submit(client, headers, **overrides) -> dict:
    payload = {"body": "反馈：希望支持 Markdown 导出"}
    payload.update(overrides)
    resp = client.post("/api/v1/feedback", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def admin_cli_env(monkeypatch, client, admin_headers):
    """Wire the CLI to the in-process server with a valid admin token."""
    token = admin_headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_ADMIN_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))


def test_cli_feedback_list_returns_items(
    runner: CliRunner, admin_cli_env, client, auth_headers, tmp_path
):
    _submit(client, auth_headers, body="反馈 A")
    _submit(client, auth_headers, body="反馈 B")

    result = runner.invoke(
        app, ["--project-root", str(tmp_path), "feedback", "list"]
    )
    assert result.exit_code == 0, result.output

    data = yaml.safe_load(result.output)
    assert data["total"] >= 2
    bodies = {item["body"] for item in data["items"]}
    assert {"反馈 A", "反馈 B"} <= bodies


def test_cli_feedback_list_filters(
    runner: CliRunner, admin_cli_env, client, auth_headers, tmp_path
):
    _submit(client, auth_headers, body="bug 反馈", category="bug")
    _submit(client, auth_headers, body="一条建议", category="suggestion")

    result = runner.invoke(
        app,
        ["--project-root", str(tmp_path), "feedback", "list", "--category", "bug"],
    )
    assert result.exit_code == 0, result.output

    data = yaml.safe_load(result.output)
    assert data["total"] == 1
    assert data["items"][0]["category"] == "bug"


def test_cli_feedback_get(
    runner: CliRunner, admin_cli_env, client, auth_headers, tmp_path
):
    fb = _submit(client, auth_headers, body="单条反馈")

    result = runner.invoke(
        app, ["--project-root", str(tmp_path), "feedback", "get", fb["id"]]
    )
    assert result.exit_code == 0, result.output

    data = yaml.safe_load(result.output)
    assert data["id"] == fb["id"]
    assert data["body"] == "单条反馈"


def test_cli_feedback_update(
    runner: CliRunner, admin_cli_env, client, auth_headers, tmp_path
):
    fb = _submit(client, auth_headers, body="待处理")

    result = runner.invoke(
        app,
        [
            "--project-root",
            str(tmp_path),
            "feedback",
            "update",
            fb["id"],
            "--status",
            "resolved",
            "--category",
            "bug",
        ],
    )
    assert result.exit_code == 0, result.output

    data = yaml.safe_load(result.output)
    assert data["status"] == "resolved"
    assert data["category"] == "bug"


def test_cli_feedback_list_missing_admin_token(
    runner: CliRunner, monkeypatch, tmp_path
):
    """No admin token (env or file) → clean non-zero exit with a clear message.

    ``HOME`` is pointed at ``tmp_path`` so ``~/.map/admin.yaml`` cannot
    leak a host token into the test.
    """
    monkeypatch.delenv("MAP_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.setenv("HOME", str(tmp_path))

    result = runner.invoke(
        app, ["--project-root", str(tmp_path), "feedback", "list"]
    )
    assert result.exit_code != 0
    assert "Admin token required" in result.output


def test_cli_feedback_list_missing_api_url(
    runner: CliRunner, monkeypatch, tmp_path
):
    """No ``.map/`` project and no ``MAP_API_URL`` → fails fast. api_url is
    resolved before the token, so this is deterministic."""
    monkeypatch.delenv("MAP_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("MAP_API_URL", raising=False)

    result = runner.invoke(
        app, ["--project-root", str(tmp_path), "feedback", "list"]
    )
    assert result.exit_code != 0
    assert "API URL" in result.output or "api_url" in result.output.lower()


def test_cli_feedback_submit_does_not_require_admin(
    runner: CliRunner, monkeypatch, client, auth_headers, tmp_path
):
    """Regression guard: ``feedback submit`` stays on the persona path and
    must NOT require the admin token (only a regular agent token)."""
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.delenv("MAP_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))

    result = runner.invoke(
        app,
        ["--project-root", str(tmp_path), "feedback", "submit", "--body", "回归反馈"],
    )
    assert result.exit_code == 0, result.output
