"""CLI tests for `map agent ...` sub-app (arch experiment 0519e2a3 PR3).

Pins:
* ``cli/commands/agent.py`` registers ``agent_app = typer.Typer()`` and
  ``cli/main.py`` exposes it under ``map agent ...``.
* ``map agent show`` mirrors ``map me`` / ``map persona whoami`` (no
  behavioral change for the persona surface).
* ``map agent list`` and ``map agent escalation-target`` round-trip
  through the corresponding ``/api/v1/agents`` endpoints via
  ``MAPTestClientTransport`` (real FastAPI app, no HTTP).
* ``map persona list`` regression — must stay green.
"""

from __future__ import annotations

import os

import pytest
import yaml
from map_client import project_config
from map_client.testing import MAPTestClientTransport
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app

pytestmark = pytest.mark.slow


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched_cli(monkeypatch, client, auth_headers):
    """Persona-mode transport for the read paths."""
    token = auth_headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.delenv("MAP_PROJECT_KEY", raising=False)
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))
    monkeypatch.setattr(cli_main, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(project_config, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "map_client.config.load_config",
        lambda *args, **kwargs: {
            "api_url": "http://test",
            "token": os.environ.get("MAP_TOKEN", token),
            "project_key": None,
        },
    )


@pytest.fixture
def patched_admin_cli(monkeypatch, client, admin_headers):
    """Admin-mode transport for the ``agent register`` path."""
    token = admin_headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_ADMIN_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))


# ---------------------------------------------------------------------------
# surface
# ---------------------------------------------------------------------------


def test_agent_subapp_registered_in_map_help(runner: CliRunner):
    """``map --help`` lists ``agent`` next to ``persona`` / ``project``."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "  agent " in result.output


def test_agent_help_lists_expected_subcommands(runner: CliRunner):
    """``map agent --help`` shows show / list / escalation-target / register."""
    result = runner.invoke(app, ["agent", "--help"])
    assert result.exit_code == 0, result.output
    for cmd in ("show", "list", "escalation-target", "register"):
        assert cmd in result.output, f"missing agent subcommand: {cmd}"


# ---------------------------------------------------------------------------
# round-trip through the real FastAPI app
# ---------------------------------------------------------------------------


def test_agent_show_returns_current_identity(runner: CliRunner, patched_cli, auth_headers):
    """``map agent show`` calls GET /api/v1/agents/me and prints the result."""
    result = runner.invoke(app, ["agent", "show"])
    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert payload is not None
    assert "id" in payload
    assert "name" in payload
    assert "role" in payload


def test_agent_list_returns_agents_visible_to_caller(
    runner: CliRunner, patched_cli, client, admin_headers, project
):
    """``map agent list`` calls GET /api/v1/agents and prints the array.

    Admin registers a sentinel agent first; we then assert it appears
    in the listing. (Admin fixtures see every agent in the project.)
    """
    resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": "cli-agent-list-sentinel",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert resp.status_code == 201, resp.text

    result = runner.invoke(app, ["agent", "list"])
    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert isinstance(payload, list)
    names = {a["name"] for a in payload}
    assert "cli-agent-list-sentinel" in names


def test_agent_list_filter_by_project(
    runner: CliRunner, patched_cli, client, admin_headers, project
):
    """``--project-id`` filters the listing."""
    from tests._frontmatter import make_valid_plan  # noqa: F401  (kept for future filter tests)

    resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": "cli-agent-filter-sentinel",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert resp.status_code == 201, resp.text

    result = runner.invoke(
        app, ["agent", "list", "--project-id", project["id"]]
    )
    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert isinstance(payload, list)
    assert any(a["name"] == "cli-agent-filter-sentinel" for a in payload)


def test_agent_escalation_target_resolves_tier(runner: CliRunner, patched_cli):
    """``map agent escalation-target`` calls GET /agents/me/escalation-target."""
    result = runner.invoke(app, ["agent", "escalation-target"])
    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert "tier" in payload
    assert "escalation_target_id" in payload
    assert "escalation_label" in payload


def test_agent_escalation_target_with_experiment_id(
    runner: CliRunner, patched_cli, client, project, auth_headers
):
    """Passing --experiment-id is forwarded as a query param."""
    from tests._frontmatter import make_valid_plan

    exp_resp = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={"title": "esc-target-exp", "plan": {"content_md": make_valid_plan(body="p")}},
    )
    assert exp_resp.status_code == 201, exp_resp.text
    exp_id = exp_resp.json()["id"]

    result = runner.invoke(app, ["agent", "escalation-target", "--experiment-id", exp_id])
    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert payload["experiment_id"] == exp_id


def test_agent_register_creates_agent_via_admin_token(
    runner: CliRunner, patched_admin_cli, project
):
    """``map agent register`` (admin mode) hits POST /api/v1/agents."""
    result = runner.invoke(
        app,
        [
            "agent",
            "register",
            "--name",
            "cli-agent-register-sentinel",
            "--role",
            "agent",
            "--project-key",
            project["project_key"],
        ],
    )
    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert payload["name"] == "cli-agent-register-sentinel"
    assert payload["role"] == "agent"
    assert payload["project_id"] == project["id"]
    assert "api_token" in payload


# ---------------------------------------------------------------------------
# bad UUID input — fail loudly before the SDK call
# ---------------------------------------------------------------------------


def test_agent_escalation_target_rejects_non_uuid_experiment_id(
    runner: CliRunner, patched_cli
):
    result = runner.invoke(
        app, ["agent", "escalation-target", "--experiment-id", "not-a-uuid"]
    )
    assert result.exit_code != 0
    assert "experiment-id" in result.output or "UUID" in result.output


# ---------------------------------------------------------------------------
# regression: persona surface must not regress
# ---------------------------------------------------------------------------


def test_persona_list_still_works(runner: CliRunner, tmp_path, monkeypatch):
    """``map persona list`` must keep working — PR3 is additive only.

    ``persona list`` reads ``.map/config.yaml`` via ``_require_map_dir``.
    Stage a minimal config + agents.yaml under ``tmp_path`` and point
    ``--project-root`` at it. We do NOT patch ``find_map_dir`` here —
    the test depends on the real walk-up behavior, which we drive by
    passing ``--project-root`` so the resolver starts from ``tmp_path``.
    """
    map_dir = tmp_path / ".map"
    map_dir.mkdir()
    (map_dir / "config.yaml").write_text(
        "api_url: http://test\n"
        "project_key: test-project\n"
        "default_persona: host\n",
        encoding="utf-8",
    )
    (map_dir / "agents.yaml").write_text(
        "personas:\n"
        "  host:\n"
        "    agent_name: test-host\n"
        "    description: 'host test'\n"
        "    tokens:\n"
        "      host: dummy\n",
        encoding="utf-8",
    )
    # tokens.local.yaml is the source-of-truth for tokens on disk
    (map_dir / "tokens.local.yaml").write_text(
        "tokens:\n  host: dummy\n", encoding="utf-8"
    )

    result = runner.invoke(
        app, ["persona", "list", "--project-root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert isinstance(payload, list)
    assert any(p["persona"] == "host" for p in payload)


def test_me_alias_still_works(runner: CliRunner, patched_cli):
    """``map me`` alias for ``persona whoami`` must keep working."""
    result = runner.invoke(app, ["me"])
    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert "id" in payload
