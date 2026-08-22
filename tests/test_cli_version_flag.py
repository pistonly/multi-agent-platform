"""``map --version`` — CLI self-reported version (ergonomics fix).

The CLI previously had no way to report its own version (no
``--version`` option, no ``version`` subcommand). Version had to be
inferred via ``pip show`` or the server OpenAPI spec, which for Agent
consumers violates the v0.12 principle "identifiers the platform
emits, the platform must parse".

Pins:
* exit code 0 and ``map <semver>`` shape on stdout
* eager short-circuit: works with a trailing subcommand and without
  touching the network / persona resolution
"""

from __future__ import annotations

import re

import pytest
import typer
from typer.testing import CliRunner

import cli.main as cli_main
from cli.main import app

runner = CliRunner()

_SEMVER_LINE = r"map \d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?"


def test_version_flag_prints_semver_and_exits_zero() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert re.fullmatch(_SEMVER_LINE, result.output.strip()), (
        f"unexpected --version output: {result.output!r}"
    )


def test_version_flag_short_circuits_trailing_subcommand() -> None:
    """``map --version topic list`` must print the version and exit 0.

    The eager callback fires before subcommand dispatch, so no client
    resolution / network access happens.
    """
    result = runner.invoke(app, ["--version", "topic", "list"])
    assert result.exit_code == 0, result.output
    assert re.fullmatch(_SEMVER_LINE, result.output.strip()), (
        f"unexpected --version output: {result.output!r}"
    )


def test_version_flag_reports_map_sdk_single_source() -> None:
    """The printed version must equal ``map_sdk.__version__`` exactly.

    map_sdk is the in-tree source of truth (pinned to pyproject by
    ``test_eng_version_single_source``). The deterministic ordering
    matters: installed metadata goes stale after a version bump in
    editable installs (observed locally: ``map 0.3.2`` while the tree
    was at 0.4.0).
    """
    import map_sdk

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"map {map_sdk.__version__}"


def test_main_fails_fast_when_map_fs_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stale editable installs must fail on the FIRST command, not deep inside.

    ``pip install -e .`` freezes the editable package map at install time;
    when a later commit adds a top-level SDK package (``map_fs``), the old
    finder can import ``cli`` but not ``map_fs``. Every ``map_fs`` import in
    the command layer is lazy, so the drift used to surface only as a raw
    ``ModuleNotFoundError`` traceback inside FS-scanning commands (observed
    locally: ``map topic list`` crashed a week after the package landed).

    ``sys.modules["map_fs"] = None`` makes ``import map_fs`` raise
    ImportError, simulating the stale finder. The startup probe must turn
    that into a clean stderr error with a recovery hint and exit 1 —
    including for ``map --version``, by design (a broken install should be
    loud on every invocation).
    """
    import sys

    monkeypatch.setitem(sys.modules, "map_fs", None)
    monkeypatch.setattr(sys, "argv", ["map", "--version"])
    with pytest.raises(typer.Exit) as excinfo:
        cli_main.main()
    assert excinfo.value.exit_code == 1
    err = capsys.readouterr().err
    assert "'map_fs'" in err
    assert "pip install -e ." in err


def test_runtime_imports_probe_passes_in_healthy_env() -> None:
    """The startup probe is a no-op when ``map_fs`` is importable."""
    cli_main._verify_runtime_imports()  # must not raise
