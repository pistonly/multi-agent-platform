"""CLI ``--format json`` error envelope contract (experiment 1561729 I1(e)).

Pins plan (e) acceptance:

- ``--format json`` mode emits a JSON envelope to stderr with
  ``{error_code, message, hint, retryable, recovery_command}``.
- ``--format yaml`` (default) still renders the human-friendly Error /
  Hint / Escalation lines.
- The CLI calls ``/agents/me/escalation-target`` only on
  STATE_MACHINE.* / REVIEW_* errors; the endpoint failure must not
  break the error rendering itself.

We stub the SDK methods rather than going through real fixtures —
keeps the test deterministic and independent of the API layer's state
machine.
"""

from __future__ import annotations

import json
import uuid

import pytest
from map_client import project_config
from map_client.exceptions import MAPHTTPError
from map_client.testing import MAPTestClientTransport
from map_types.schemas import EscalationTargetRead
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app
from sdk.python.map_client import MAPClient


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _reset_cli_options(monkeypatch):
    """Reset ``_cli_options`` to yaml defaults so tests don't pollute each other.

    ``app.callback()`` mutates the module-level ``_cli_options`` dict with
    whatever the test passes via ``--format``. Without this autouse reset,
    a test that ran with ``--format json`` would leak into the next test
    and cause yaml-mode assertions to see JSON envelopes.
    """
    monkeypatch.setattr(
        cli_main,
        "_cli_options",
        {"persona": None, "project_root": None, "format": "yaml"},
    )


@pytest.fixture
def patched_cli(monkeypatch):
    """Patch CLI bootstrap env; tests stub SDK methods as needed."""
    monkeypatch.setenv("MAP_TOKEN", "stub-token")
    monkeypatch.setenv("MAP_API_URL", "http://test")
    monkeypatch.delenv("MAP_PROJECT_KEY", raising=False)
    monkeypatch.setattr(cli_main, "_transport", MAPTestClientTransport.__new__(MAPTestClientTransport))
    monkeypatch.setattr(cli_main, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(project_config, "find_map_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "map_client.config.load_config",
        lambda *args, **kwargs: {"api_url": "http://test", "token": "stub-token", "project_key": None},
    )


def _state_machine_error() -> MAPHTTPError:
    return MAPHTTPError(
        status_code=409,
        detail="Cannot start draft experiment: must be in review phase",
        error_code="STATE_MACHINE_INVALID_PHASE",
        hint="submit for review first",
        retryable=False,
    )


def _escalation_target(experiment_id=None) -> EscalationTargetRead:
    return EscalationTargetRead(
        experiment_id=experiment_id,
        escalation_target_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        escalation_label="@oncall-buddy",
        tier="current_caller",
    )


def test_cli_format_json_emits_envelope_on_state_machine_error(
    runner, patched_cli, monkeypatch
) -> None:
    """--format json + STATE_MACHINE.* error → JSON envelope on stderr."""

    def fake_start(self, experiment_id):
        raise _state_machine_error()

    def fake_escalation(self, experiment_id=None):
        return _escalation_target(experiment_id)

    monkeypatch.setattr(MAPClient, "start_experiment", fake_start)
    monkeypatch.setattr(MAPClient, "get_escalation_target", fake_escalation)

    result = runner.invoke(
        app,
        [
            "--format",
            "json",
            "experiment",
            "start",
            "--id",
            str(uuid.uuid4()),
        ],
    )
    assert result.exit_code != 0, (
        f"expected start to fail; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    # Find the JSON envelope in stderr (the last JSON-looking line).
    stderr_lines = [
        line.strip() for line in result.stderr.splitlines() if line.strip()
    ]
    envelope_lines = [line for line in stderr_lines if line.startswith("{")]
    assert envelope_lines, (
        f"--format json must emit a JSON envelope, got stderr={result.stderr!r}"
    )
    envelope = json.loads(envelope_lines[-1])
    # Schema pins: every field present. ``docs_url`` is the 8a8822b5 (b)
    # optional human-doc-link field; it may be null but must always be
    # declared on the envelope.
    assert set(envelope.keys()) == {
        "error_code",
        "message",
        "hint",
        "docs_url",
        "retryable",
        "recovery_command",
    }
    assert envelope["error_code"] == "STATE_MACHINE_INVALID_PHASE"
    assert envelope["hint"] == "submit for review first"
    # recovery_command mirrors hint per design.
    assert envelope["recovery_command"] == envelope["hint"]
    # message carries the exception detail.
    assert "draft experiment" in envelope["message"]


def test_cli_format_yaml_includes_escalation_line(
    runner, patched_cli, monkeypatch
) -> None:
    """--format yaml + STATE_MACHINE.* error → Escalation line on stderr."""

    def fake_start(self, experiment_id):
        raise _state_machine_error()

    def fake_escalation(self, experiment_id=None):
        return _escalation_target(experiment_id)

    monkeypatch.setattr(MAPClient, "start_experiment", fake_start)
    monkeypatch.setattr(MAPClient, "get_escalation_target", fake_escalation)

    result = runner.invoke(
        app,
        ["experiment", "start", "--id", str(uuid.uuid4())],
    )
    assert result.exit_code != 0, (
        f"expected start to fail; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    stderr = result.stderr
    assert "Error" in stderr
    assert "STATE_MACHINE_INVALID_PHASE" in stderr
    assert "Hint: submit for review first" in stderr
    assert "Escalation: @oncall-buddy (tier=current_caller)" in stderr


def test_cli_envelope_does_not_break_when_escalation_endpoint_fails(
    runner, patched_cli, monkeypatch
) -> None:
    """--format yaml: if escalation endpoint errors, error still renders."""

    def fake_start(self, experiment_id):
        raise _state_machine_error()

    def boom(self, experiment_id=None):
        raise RuntimeError("simulated escalation endpoint outage")

    monkeypatch.setattr(MAPClient, "start_experiment", fake_start)
    monkeypatch.setattr(MAPClient, "get_escalation_target", boom)

    result = runner.invoke(
        app,
        ["experiment", "start", "--id", str(uuid.uuid4())],
    )
    # Exit code must still be non-zero (the original error fired).
    assert result.exit_code != 0
    # Error rendering still happens.
    assert "Error" in result.stderr
    assert "STATE_MACHINE_INVALID_PHASE" in result.stderr
    # No Escalation line because lookup failed — but no crash either.
    assert "Escalation:" not in result.stderr


def test_cli_envelope_json_unaffected_by_escalation_endpoint_failure(
    runner, patched_cli, monkeypatch
) -> None:
    """--format json: envelope still emitted even if escalation lookup fails.

    The JSON path doesn't call the escalation endpoint (only the YAML
    path does), so the envelope is unaffected — verify explicitly.
    """

    def fake_start(self, experiment_id):
        raise _state_machine_error()

    def boom(self, experiment_id=None):
        raise RuntimeError("simulated escalation outage")

    monkeypatch.setattr(MAPClient, "start_experiment", fake_start)
    monkeypatch.setattr(MAPClient, "get_escalation_target", boom)

    result = runner.invoke(
        app,
        ["--format", "json", "experiment", "start", "--id", str(uuid.uuid4())],
    )
    assert result.exit_code != 0
    envelope_lines = [
        line.strip()
        for line in result.stderr.splitlines()
        if line.strip().startswith("{")
    ]
    assert envelope_lines, result.stderr
    envelope = json.loads(envelope_lines[-1])
    assert set(envelope.keys()) == {
        "error_code",
        "message",
        "hint",
        "docs_url",
        "retryable",
        "recovery_command",
    }


def test_cli_value_error_envelope_json(
    runner, patched_cli, monkeypatch
) -> None:
    """--format json + ValueError (no MAPHTTPError) still emits envelope.

    The CLI catches ValueError too (used for argument validation) and
    must still emit a JSON envelope with null fields rather than free-text.
    """

    def fake_start(self, experiment_id):
        raise ValueError("experiment id is malformed")

    monkeypatch.setattr(MAPClient, "start_experiment", fake_start)

    result = runner.invoke(
        app,
        ["--format", "json", "experiment", "start", "--id", str(uuid.uuid4())],
    )
    assert result.exit_code != 0
    envelope_lines = [
        line.strip()
        for line in result.stderr.splitlines()
        if line.strip().startswith("{")
    ]
    assert envelope_lines, result.stderr
    envelope = json.loads(envelope_lines[-1])
    assert envelope["error_code"] is None
    assert envelope["hint"] is None
    assert envelope["retryable"] is None
    assert envelope["recovery_command"] is None
    assert "malformed" in envelope["message"]
