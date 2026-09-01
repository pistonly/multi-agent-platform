"""8a8822b5 (a): N=2 yaml hard-cutover detection.

Pins plan v2 (a) acceptance:

* Pre-N=2 release: ``--format yaml`` / ``MAP_CLI_FORMAT=yaml`` /
  ``.map/config.yaml cli.default_format: yaml`` continue to behave as
  before (yaml default output, no cutover warnings).
* Post-N=2 release (``MAP_CLI_RELEASE_VERSION >= 1.0``): ``yaml`` is
  force-overridden to ``json`` regardless of flag / env, with a one-shot
  stderr warning. ``.map/config.yaml cli.default_format: yaml`` triggers
  the same path even when the user did not pass any format flag.
* Helper functions are pure (no ``typer.echo``) so they are unit-testable
  in isolation.
"""

from __future__ import annotations

import json
import textwrap
import uuid

import pytest
from map_client.exceptions import MAPNotFoundError
from typer.testing import CliRunner

import cli.main as cli_main

# T33: n2 cutoff helpers moved to ``cli.subcommand_format`` (imported into
# ``cli.main`` for the global callback; tests pin them at the source).
from cli.main import app
from cli.subcommand_format import (
    _N2_RELEASE_MAJOR,
    _apply_n2_hard_cutover,
    _is_n2_released,
    _parse_release_version,
    _project_cli_default_format,
)

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

    # Replace the client context manager so ``_resolve_project`` / token
    # loading does not short-circuit our 404 path before cutover runs.
    from contextlib import contextmanager

    from map_client import MAPClient

    fake_client = MAPClient("http://test", "fake", transport=transport)

    @contextmanager
    def _proxy_client_ctx():
        yield fake_client

    monkeypatch.setattr(cli_main, "_client_ctx", _proxy_client_ctx)


# ---- pure helper unit tests -----------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0.9", (0, 9)),
        ("1.0", (1, 0)),
        ("1.5", (1, 5)),
        ("2.0", (2, 0)),
        ("1", (1, 0)),
        ("10.20", (10, 20)),
        (None, (0, 9)),
        ("", (0, 9)),
        ("  ", (0, 9)),
        ("xyz", (0, 9)),
        ("1.x", (1, 0)),  # bad minor treated as 0
    ],
)
def test_parse_release_version(raw, expected):
    assert _parse_release_version(raw) == expected


def test_n2_release_major_constant_is_1():
    """The cutoff constant is the source of truth for N=2 hard-cutover."""
    assert _N2_RELEASE_MAJOR == 1


def test_is_n2_released_defaults_to_false(monkeypatch):
    monkeypatch.delenv("MAP_CLI_RELEASE_VERSION", raising=False)
    assert _is_n2_released() is False


@pytest.mark.parametrize("raw", ["1.0", "1.1", "2.0", "10.0"])
def test_is_n2_released_true_for_major_at_or_above(monkeypatch, raw):
    monkeypatch.setenv("MAP_CLI_RELEASE_VERSION", raw)
    assert _is_n2_released() is True


@pytest.mark.parametrize("raw", ["0.9", "0.0", "0.99"])
def test_is_n2_released_false_for_below(monkeypatch, raw):
    monkeypatch.setenv("MAP_CLI_RELEASE_VERSION", raw)
    assert _is_n2_released() is False


def test_is_n2_released_handles_garbage(monkeypatch):
    monkeypatch.setenv("MAP_CLI_RELEASE_VERSION", "garbage")
    # Garbage falls back to (0, 9) which is below N=2.
    assert _is_n2_released() is False


# ---- _project_cli_default_format ------------------------------------------


def test_project_cli_default_format_no_project_root():
    assert _project_cli_default_format(None) is None


def test_project_cli_default_format_missing_config(tmp_path):
    assert _project_cli_default_format(tmp_path) is None


def test_project_cli_default_format_missing_cli_block(tmp_path):
    (tmp_path / ".map").mkdir()
    (tmp_path / ".map" / "config.yaml").write_text(
        "project_key: foo\n", encoding="utf-8"
    )
    assert _project_cli_default_format(tmp_path) is None


def test_project_cli_default_format_yaml(tmp_path):
    (tmp_path / ".map").mkdir()
    (tmp_path / ".map" / "config.yaml").write_text(
        textwrap.dedent(
            """
            project_key: foo
            cli:
              default_format: yaml
            """
        ).strip(),
        encoding="utf-8",
    )
    assert _project_cli_default_format(tmp_path) == "yaml"


def test_project_cli_default_format_json(tmp_path):
    (tmp_path / ".map").mkdir()
    (tmp_path / ".map" / "config.yaml").write_text(
        textwrap.dedent(
            """
            project_key: foo
            cli:
              default_format: json
            """
        ).strip(),
        encoding="utf-8",
    )
    assert _project_cli_default_format(tmp_path) == "json"


def test_project_cli_default_format_non_string_value(tmp_path):
    (tmp_path / ".map").mkdir()
    (tmp_path / ".map" / "config.yaml").write_text(
        "cli:\n  default_format: 123\n", encoding="utf-8"
    )
    assert _project_cli_default_format(tmp_path) is None


def test_project_cli_default_format_corrupt_yaml(tmp_path):
    (tmp_path / ".map").mkdir()
    (tmp_path / ".map" / "config.yaml").write_text(
        "{ this is not valid yaml", encoding="utf-8"
    )
    assert _project_cli_default_format(tmp_path) is None


# ---- _apply_n2_hard_cutover pure logic ------------------------------------


def test_apply_n2_cutover_pre_n2_yaml_passes_through(monkeypatch):
    monkeypatch.delenv("MAP_CLI_RELEASE_VERSION", raising=False)
    new_fmt, src, warns = _apply_n2_hard_cutover(
        current_format="yaml", current_source="default", project_root=None
    )
    assert new_fmt == "yaml"
    assert src == "default"
    assert warns == []


def test_apply_n2_cutover_post_n2_yaml_forces_json(monkeypatch):
    monkeypatch.setenv("MAP_CLI_RELEASE_VERSION", "1.0")
    new_fmt, src, warns = _apply_n2_hard_cutover(
        current_format="yaml", current_source="explicit --format", project_root=None
    )
    assert new_fmt == "json"
    assert src == "n2-release-cutover"
    assert len(warns) == 1
    assert "yaml output format is removed in N=2" in warns[0]


def test_apply_n2_cutover_post_n2_json_unchanged(monkeypatch):
    monkeypatch.setenv("MAP_CLI_RELEASE_VERSION", "1.0")
    new_fmt, src, warns = _apply_n2_hard_cutover(
        current_format="json", current_source="default", project_root=None
    )
    assert new_fmt == "json"
    assert src == "default"
    assert warns == []


def test_apply_n2_cutover_post_n2_config_yaml_forces_json(monkeypatch, tmp_path):
    monkeypatch.setenv("MAP_CLI_RELEASE_VERSION", "1.0")
    (tmp_path / ".map").mkdir()
    (tmp_path / ".map" / "config.yaml").write_text(
        "cli:\n  default_format: yaml\n", encoding="utf-8"
    )
    new_fmt, src, warns = _apply_n2_hard_cutover(
        current_format="json", current_source="default", project_root=tmp_path
    )
    assert new_fmt == "json"
    # The yaml-deprecation warning wins over the bare json no-op.
    assert src == "n2-release-cutover"
    assert len(warns) == 1
    assert "cli.default_format: yaml" in warns[0]


def test_apply_n2_cutover_post_n2_config_json_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("MAP_CLI_RELEASE_VERSION", "1.0")
    (tmp_path / ".map").mkdir()
    (tmp_path / ".map" / "config.yaml").write_text(
        "cli:\n  default_format: json\n", encoding="utf-8"
    )
    new_fmt, src, warns = _apply_n2_hard_cutover(
        current_format="yaml", current_source="explicit --format", project_root=tmp_path
    )
    # Config says json, so the only cutover signal is the explicit flag.
    assert new_fmt == "json"
    assert src == "n2-release-cutover"
    # Only the "yaml output format is removed" warning, not the config one.
    assert len(warns) == 1
    assert "yaml output format is removed in N=2" in warns[0]


# ---- end-to-end CLI behavior ----------------------------------------------


def test_pre_n2_yaml_default_unchanged(
    runner, patched_cli_maphttp_error, monkeypatch
):
    """Pre-N=2 default still emits yaml error output."""
    monkeypatch.delenv("MAP_CLI_RELEASE_VERSION", raising=False)
    result = runner.invoke(
        app, ["experiment", "show", "--id", str(uuid.uuid4())]
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    assert "Error 404" in stderr
    assert "removed in N=2" not in stderr
    assert not any(ln.startswith("{") for ln in stderr.splitlines() if ln.strip())


def test_post_n2_yaml_flag_forces_json_with_warning(
    runner, patched_cli_maphttp_error, monkeypatch
):
    monkeypatch.setenv("MAP_CLI_RELEASE_VERSION", "1.0")
    result = runner.invoke(
        app, ["--format", "yaml", "experiment", "show", "--id", str(uuid.uuid4())]
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    assert "yaml output format is removed in N=2" in stderr
    envelope_line = next(
        (ln for ln in stderr.splitlines() if ln.startswith("{")), None
    )
    assert envelope_line is not None, stderr
    payload = json.loads(envelope_line)
    assert payload["ok"] is False
    parsed = payload["error"]
    assert "error_code" in parsed
    # format_source records the cutover decision for diagnostics.
    assert cli_main._cli_options["format_source"] == "n2-release-cutover"


def test_post_n2_env_yaml_forces_json_with_warning(
    runner, patched_cli_maphttp_error, monkeypatch
):
    monkeypatch.setenv("MAP_CLI_RELEASE_VERSION", "1.0")
    monkeypatch.setenv("MAP_CLI_FORMAT", "yaml")
    result = runner.invoke(
        app, ["experiment", "show", "--id", str(uuid.uuid4())]
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    assert "yaml output format is removed in N=2" in stderr
    envelope_line = next(
        (ln for ln in stderr.splitlines() if ln.startswith("{")), None
    )
    assert envelope_line is not None, stderr


def test_post_n2_env_json_passes_through(
    runner, patched_cli_maphttp_error, monkeypatch
):
    """Post-N=2 + explicit json → no cutover warning."""
    monkeypatch.setenv("MAP_CLI_RELEASE_VERSION", "1.0")
    result = runner.invoke(
        app, ["--format", "json", "experiment", "show", "--id", str(uuid.uuid4())]
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    assert "removed in N=2" not in stderr
    envelope_line = next(
        (ln for ln in stderr.splitlines() if ln.startswith("{")), None
    )
    assert envelope_line is not None, stderr


def test_post_n2_config_yaml_forces_json_with_config_warning(
    runner, patched_cli_maphttp_error, monkeypatch, tmp_path
):
    """Post-N=2 + ``cli.default_format: yaml`` in ``.map/config.yaml`` triggers
    the cutover even when no flag / env is given."""
    monkeypatch.setenv("MAP_CLI_RELEASE_VERSION", "1.0")
    (tmp_path / ".map").mkdir()
    (tmp_path / ".map" / "config.yaml").write_text(
        "project_key: test\ncli:\n  default_format: yaml\n", encoding="utf-8"
    )
    result = runner.invoke(
        app,
        [
            "--project-root",
            str(tmp_path),
            "experiment",
            "show",
            "--id",
            str(uuid.uuid4()),
        ],
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    assert "cli.default_format: yaml" in stderr
    envelope_line = next(
        (ln for ln in stderr.splitlines() if ln.startswith("{")), None
    )
    assert envelope_line is not None, stderr


def test_post_n2_no_flag_no_env_defaults_to_json(
    runner, patched_cli_maphttp_error, monkeypatch
):
    """Post-N=2 + no flag / no env → default falls through to json silently
    (no spurious cutover warning because the user didn't ask for yaml)."""
    monkeypatch.delenv("MAP_CLI_FORMAT", raising=False)
    monkeypatch.setenv("MAP_CLI_RELEASE_VERSION", "1.0")
    result = runner.invoke(
        app, ["experiment", "show", "--id", str(uuid.uuid4())]
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    # No cutover warning because the implicit default doesn't request yaml.
    assert "removed in N=2" not in stderr
    assert "cli.default_format" not in stderr
    # But the format_source must still be json (cutover applied to default).
    assert cli_main._cli_options["format"] == "json"


def test_post_n2_config_json_no_warning(
    runner, patched_cli_maphttp_error, monkeypatch, tmp_path
):
    """Post-N=2 + ``cli.default_format: json`` → no warning."""
    monkeypatch.setenv("MAP_CLI_RELEASE_VERSION", "1.0")
    (tmp_path / ".map").mkdir()
    (tmp_path / ".map" / "config.yaml").write_text(
        "project_key: test\ncli:\n  default_format: json\n", encoding="utf-8"
    )
    result = runner.invoke(
        app,
        [
            "--project-root",
            str(tmp_path),
            "experiment",
            "show",
            "--id",
            str(uuid.uuid4()),
        ],
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    assert "removed in N=2" not in stderr
    assert "cli.default_format" not in stderr


def test_pre_n2_config_yaml_no_cutover_warning(
    runner, patched_cli_maphttp_error, monkeypatch, tmp_path
):
    """Pre-N=2 + ``cli.default_format: yaml`` → yaml is honored, no warning."""
    monkeypatch.delenv("MAP_CLI_RELEASE_VERSION", raising=False)
    (tmp_path / ".map").mkdir()
    (tmp_path / ".map" / "config.yaml").write_text(
        "project_key: test\ncli:\n  default_format: yaml\n", encoding="utf-8"
    )
    result = runner.invoke(
        app,
        [
            "--project-root",
            str(tmp_path),
            "experiment",
            "show",
            "--id",
            str(uuid.uuid4()),
        ],
    )
    assert result.exit_code != 0
    stderr = result.stderr or ""
    assert "removed in N=2" not in stderr
    assert "cli.default_format" not in stderr
    # Still yaml mode.
    assert "Error 404" in stderr
