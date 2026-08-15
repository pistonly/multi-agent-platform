"""v0.12 M54A: subcommand-level ``--format`` option.

Pins the E1 fix acceptance (``docs/prd/v0.12.md``):

* ``map experiment show --format json --id X`` works — the flag no longer
  has to sit before the subcommand name, and is equivalent to the global
  spelling ``map --format json experiment show --id X``.
* Injection reaches **nested** sub-apps (``experiment`` → ``review``).
* An explicit subcommand value overrides the global ``--format`` /
  ``--json`` flag and ``MAP_CLI_FORMAT`` env (subcommand wins, warning on
  stderr), matching the PRD compat rule.
* Unknown values exit 2 with the same friendly error as the global path.
* ``--help`` of any leaf command lists the injected ``--format`` option.

Observability strategy mirrors ``tests/test_cli_format_priority.py``: a
transport stub raises ``MAPNotFoundError(404)`` so the error envelope's
format (json object on stderr vs human-readable yaml line) reveals the
resolved output format without needing live data.
"""

from __future__ import annotations

import json
import uuid

import pytest
from map_client.exceptions import MAPNotFoundError
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app

# ---- fixtures --------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched_cli_maphttp_error(monkeypatch):
    """Transport raises MAPNotFoundError(404) on every call."""

    class _RaisingTransport:
        def __init__(self) -> None:
            self.calls = 0

        def handle_request(self, request):
            self.calls += 1
            raise MAPNotFoundError(404, "Experiment not found")

    transport = _RaisingTransport()
    monkeypatch.setattr(cli_main, "_transport", transport)
    monkeypatch.setenv("MAP_TOKEN", "fake")
    monkeypatch.setenv("MAP_API_URL", "http://test")


def _json_envelope(stderr: str) -> dict:
    """Extract the error envelope: ``{"ok": false, "error": {...}}`` → inner dict."""
    line = next((ln for ln in stderr.splitlines() if ln.startswith("{")), None)
    assert line is not None, stderr
    envelope = json.loads(line)
    assert envelope["ok"] is False
    return envelope["error"]


# ---- subcommand-level --format works (E1 core) -----------------------------


def test_sub_format_json_equivalent_to_global(
    runner, patched_cli_maphttp_error, monkeypatch
):
    """``experiment show --format json`` == ``--format json experiment show``."""
    monkeypatch.delenv("MAP_CLI_FORMAT", raising=False)
    exp_id = str(uuid.uuid4())

    sub = runner.invoke(app, ["experiment", "show", "--format", "json", "--id", exp_id])
    assert sub.exit_code != 0
    sub_env = _json_envelope(sub.stderr or "")

    glob = runner.invoke(
        app, ["--format", "json", "experiment", "show", "--id", exp_id]
    )
    assert glob.exit_code != 0
    glob_env = _json_envelope(glob.stderr or "")

    assert sub_env == glob_env


def test_sub_format_accepts_flag_after_other_args(
    runner, patched_cli_maphttp_error, monkeypatch
):
    """Options interleave freely: ``--id X --format json`` at the tail works."""
    monkeypatch.delenv("MAP_CLI_FORMAT", raising=False)
    result = runner.invoke(
        app,
        ["experiment", "show", "--id", str(uuid.uuid4()), "--format", "json"],
    )
    assert result.exit_code != 0
    assert _json_envelope(result.stderr or "")["message"] == "Experiment not found"


def test_nested_subcommand_gets_format_too(
    runner, patched_cli_maphttp_error, monkeypatch
):
    """Injection recurses into sub-apps: ``experiment review list --format json``."""
    monkeypatch.delenv("MAP_CLI_FORMAT", raising=False)
    result = runner.invoke(
        app,
        ["experiment", "review", "list", "--format", "json", "--id", str(uuid.uuid4())],
    )
    assert result.exit_code != 0
    assert "No such option" not in (result.stderr or "")
    assert _json_envelope(result.stderr or "")["message"] == "Experiment not found"


def test_leaf_help_lists_format_option(runner):
    """``--help`` on a (nested) leaf command shows the injected ``--format``."""
    result = runner.invoke(app, ["experiment", "review", "list", "--help"])
    assert result.exit_code == 0, result.stdout
    assert "--format" in result.stdout


# ---- precedence: subcommand wins -------------------------------------------


def test_sub_format_overrides_global_flag(
    runner, patched_cli_maphttp_error, monkeypatch
):
    """Global ``--format yaml`` + sub ``--format json`` → json + warning."""
    monkeypatch.delenv("MAP_CLI_FORMAT", raising=False)
    result = runner.invoke(
        app,
        [
            "--format",
            "yaml",
            "experiment",
            "show",
            "--format",
            "json",
            "--id",
            str(uuid.uuid4()),
        ],
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    assert "subcommand --format=json overrides global --format=yaml" in stderr
    assert _json_envelope(stderr)["message"] == "Experiment not found"


def test_sub_format_overrides_env(
    runner, patched_cli_maphttp_error, monkeypatch
):
    """``MAP_CLI_FORMAT=yaml`` + sub ``--format json`` → json + warning."""
    monkeypatch.setenv("MAP_CLI_FORMAT", "yaml")
    result = runner.invoke(
        app,
        ["experiment", "show", "--format", "json", "--id", str(uuid.uuid4())],
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    assert "subcommand --format=json overrides MAP_CLI_FORMAT=yaml" in stderr
    assert _json_envelope(stderr)


def test_sub_format_records_source(runner, patched_cli_maphttp_error, monkeypatch):
    """``_cli_options['format_source']`` marks the subcommand decision."""
    monkeypatch.delenv("MAP_CLI_FORMAT", raising=False)
    runner.invoke(
        app,
        ["experiment", "show", "--format", "json", "--id", str(uuid.uuid4())],
    )
    assert cli_main._cli_options["format"] == "json"
    assert cli_main._cli_options["format_source"] == "explicit --format (subcommand)"


# ---- validation / passthrough ----------------------------------------------


def test_unknown_sub_format_exits_2(runner, patched_cli_maphttp_error):
    """``--format xyz`` at subcommand level → exit 2 + friendly error."""
    result = runner.invoke(
        app,
        ["experiment", "show", "--format", "xyz", "--id", str(uuid.uuid4())],
    )
    assert result.exit_code == 2
    stderr = result.stderr or ""
    assert "unknown --format" in stderr
    assert "'yaml'" in stderr


def test_no_sub_format_leaves_global_resolution_intact(
    runner, patched_cli_maphttp_error, monkeypatch
):
    """Without the sub flag nothing changes: default yaml, no json envelope."""
    monkeypatch.delenv("MAP_CLI_FORMAT", raising=False)
    result = runner.invoke(
        app, ["experiment", "show", "--id", str(uuid.uuid4())]
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    assert "Error 404" in stderr
    assert not any(ln.startswith("{") for ln in stderr.splitlines() if ln.strip())
