"""8a8822b5 (f): ``MAP_CLI_FORMAT`` env var vs ``--format`` flag priority.

Pins plan v2 (f) acceptance:

* ``MAP_CLI_FORMAT=legacy`` + ``--format json`` → output json, stderr prints
  ``explicit --format overrides MAP_CLI_FORMAT`` warning, do NOT block.
* ``MAP_CLI_FORMAT=legacy`` (no flag) → falls back to yaml behavior + prints
  deprecation warning that ``legacy`` will be removed in N=2.
* ``MAP_CLI_FORMAT=json`` (no flag) → json error envelope.
* ``MAP_CLI_FORMAT=yaml`` (no flag) → yaml error envelope.
* Explicit ``--format yaml`` / ``--format json`` ignores env var entirely.
* Unknown ``--format`` value exits 2 with a friendly error.
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
def patched_cli_404(monkeypatch, client, auth_headers):
    """MAP client transport pointed at the in-process TestClient."""
    from map_client.testing import MAPTestClientTransport

    token = auth_headers["Authorization"].removeprefix("Bearer ")
    monkeypatch.setenv("MAP_TOKEN", token)
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport(client))


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


# ---- (f) env / flag priority ----------------------------------------------


def test_map_cli_format_legacy_with_explicit_json_emits_json_with_warning(
    runner, patched_cli_maphttp_error, monkeypatch
):
    """``MAP_CLI_FORMAT=legacy`` + ``--format json`` → JSON + override warning."""
    monkeypatch.setenv("MAP_CLI_FORMAT", "legacy")
    result = runner.invoke(
        app,
        [
            "--format",
            "json",
            "experiment",
            "show",
            "--id",
            str(uuid.uuid4()),
        ],
    )
    assert result.exit_code != 0
    assert "explicit --format=json overrides MAP_CLI_FORMAT=legacy" in (result.stderr or "")
    envelope_line = next(
        (ln for ln in result.stderr.splitlines() if ln.startswith("{")), None
    )
    assert envelope_line is not None, result.stderr
    payload = json.loads(envelope_line)
    assert payload["ok"] is False
    parsed = payload["error"]
    assert parsed["error_code"] is None
    assert parsed["message"] == "Experiment not found"


def test_map_cli_format_legacy_without_flag_emits_yaml_with_deprecation_warning(
    runner, patched_cli_maphttp_error, monkeypatch
):
    """``MAP_CLI_FORMAT=legacy`` alone → yaml output + deprecation warning."""
    monkeypatch.setenv("MAP_CLI_FORMAT", "legacy")
    monkeypatch.delenv("MAP_CLI_FORMAT", raising=False)
    monkeypatch.setenv("MAP_CLI_FORMAT", "legacy")
    result = runner.invoke(
        app,
        ["experiment", "show", "--id", str(uuid.uuid4())],
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    # Deprecation warning.
    assert "MAP_CLI_FORMAT=legacy is deprecated" in stderr
    assert "N=2" in stderr
    # YAML output: human-readable "Error <code>: <detail>" line.
    assert "Error 404" in stderr
    # No JSON envelope (yaml mode).
    assert not any(ln.startswith("{") for ln in stderr.splitlines() if ln.strip())


def test_map_cli_format_json_without_flag_emits_json(
    runner, patched_cli_maphttp_error, monkeypatch
):
    """``MAP_CLI_FORMAT=json`` alone → json error envelope (no warning)."""
    monkeypatch.setenv("MAP_CLI_FORMAT", "json")
    result = runner.invoke(
        app,
        ["experiment", "show", "--id", str(uuid.uuid4())],
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    # No deprecation / override warning.
    assert "deprecated" not in stderr
    assert "overrides" not in stderr
    # JSON envelope present.
    envelope_line = next(
        (ln for ln in stderr.splitlines() if ln.startswith("{")), None
    )
    assert envelope_line is not None, stderr
    payload = json.loads(envelope_line)
    assert payload["ok"] is False
    parsed = payload["error"]
    assert "error_code" in parsed
    assert "docs_url" in parsed


def test_map_cli_format_yaml_without_flag_emits_yaml(
    runner, patched_cli_maphttp_error, monkeypatch
):
    """``MAP_CLI_FORMAT=yaml`` alone → yaml error envelope (no warning)."""
    monkeypatch.setenv("MAP_CLI_FORMAT", "yaml")
    result = runner.invoke(
        app,
        ["experiment", "show", "--id", str(uuid.uuid4())],
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    assert "Error 404" in stderr
    # No JSON envelope.
    assert not any(ln.startswith("{") for ln in stderr.splitlines() if ln.strip())


def test_explicit_yaml_flag_ignores_json_env(
    runner, patched_cli_maphttp_error, monkeypatch
):
    """``MAP_CLI_FORMAT=json`` + ``--format yaml`` → yaml output, override warning."""
    monkeypatch.setenv("MAP_CLI_FORMAT", "json")
    result = runner.invoke(
        app,
        [
            "--format",
            "yaml",
            "experiment",
            "show",
            "--id",
            str(uuid.uuid4()),
        ],
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    assert "explicit --format=yaml overrides MAP_CLI_FORMAT=json" in stderr
    assert "Error 404" in stderr


def test_explicit_json_flag_ignores_yaml_env(
    runner, patched_cli_maphttp_error, monkeypatch
):
    """``MAP_CLI_FORMAT=yaml`` + ``--format json`` → json output, override warning."""
    monkeypatch.setenv("MAP_CLI_FORMAT", "yaml")
    result = runner.invoke(
        app,
        [
            "--format",
            "json",
            "experiment",
            "show",
            "--id",
            str(uuid.uuid4()),
        ],
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    assert "explicit --format=json overrides MAP_CLI_FORMAT=yaml" in stderr
    envelope_line = next(
        (ln for ln in stderr.splitlines() if ln.startswith("{")), None
    )
    assert envelope_line is not None, stderr


def test_explicit_flag_matches_env_no_warning(
    runner, patched_cli_maphttp_error, monkeypatch
):
    """``MAP_CLI_FORMAT=json`` + ``--format json`` → no override warning (idempotent)."""
    monkeypatch.setenv("MAP_CLI_FORMAT", "json")
    result = runner.invoke(
        app,
        [
            "--format",
            "json",
            "experiment",
            "show",
            "--id",
            str(uuid.uuid4()),
        ],
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    assert "overrides" not in stderr


def test_unknown_format_value_exits_2(
    runner, patched_cli_maphttp_error
):
    """``--format xyz`` → exit 2 + friendly error."""
    result = runner.invoke(
        app,
        [
            "--format",
            "xyz",
            "experiment",
            "show",
            "--id",
            str(uuid.uuid4()),
        ],
    )
    assert result.exit_code == 2
    stderr = result.stderr or ""
    assert "unknown --format" in stderr
    assert "'yaml', 'json', or 'legacy'" in stderr


def test_no_env_no_flag_defaults_to_yaml(
    runner, patched_cli_maphttp_error, monkeypatch
):
    """No env, no flag → default yaml error output."""
    monkeypatch.delenv("MAP_CLI_FORMAT", raising=False)
    result = runner.invoke(
        app,
        ["experiment", "show", "--id", str(uuid.uuid4())],
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    assert "Error 404" in stderr
    assert not any(ln.startswith("{") for ln in stderr.splitlines() if ln.strip())


def test_legacy_alias_does_not_break_in_process_testclient(
    runner, patched_cli_404, monkeypatch
):
    """End-to-end: legacy env + flag = json works against the in-process TestClient.

    This is the happy-path sanity check that plan (f) does not regress the
    pre-existing in-process test harness (``MAPTestClientTransport``).
    """
    monkeypatch.setenv("MAP_CLI_FORMAT", "legacy")
    result = runner.invoke(
        app,
        [
            "--format",
            "json",
            "experiment",
            "show",
            "--id",
            str(uuid.uuid4()),
        ],
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    assert "explicit --format=json overrides MAP_CLI_FORMAT=legacy" in stderr
    envelope_line = next(
        (ln for ln in stderr.splitlines() if ln.startswith("{")), None
    )
    assert envelope_line is not None, stderr
    payload = json.loads(envelope_line)
    assert payload["ok"] is False
    parsed = payload["error"]
    assert "error_code" in parsed
    assert "docs_url" in parsed


def test_format_source_records_decision(runner, patched_cli_maphttp_error, monkeypatch):
    """The callback records ``_cli_options['format_source']`` for diagnostics."""
    monkeypatch.delenv("MAP_CLI_FORMAT", raising=False)
    runner.invoke(
        app,
        ["--format", "json", "experiment", "show", "--id", str(uuid.uuid4())],
    )
    assert cli_main._cli_options["format"] == "json"
    assert cli_main._cli_options["format_source"] == "explicit --format"
