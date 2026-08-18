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

from typer.testing import CliRunner

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
