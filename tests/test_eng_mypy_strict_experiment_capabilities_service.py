"""Tests for eng experiment (55634575) PR5c — promote
server/services/experiment_capabilities_service.py to mypy strict.

Third services-layer module to join the strict surface (after
project_service.py in PR5 and phase_service.py in PR5b). Three
pre-existing ``dict`` type-arg gaps fixed:

* Line 200 — ``extra_updates: dict | None`` → ``dict[str, Any] | None``
  (matches ``**extra`` caller's untyped kwargs)
* Line 230 — ``update: dict`` → ``dict[str, Any]`` (used as
  ``model_copy(update=...)`` payload for ``ExperimentSummaryRead``)
* Line 254 — same ``update: dict`` → ``dict[str, Any]`` (used for
  ``ExperimentDetailRead``)

Pins:
1. ``server/services/experiment_capabilities_service.py`` passes
   ``mypy --strict``.
2. ``pyproject.toml`` lists all 7 modules in one override block under
   ``strict = true``.
3. Baseline mypy (project config) clean.
4. Regression guards: no bare ``dict`` annotations remain.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
PYPROJECT = f"{PROJECT_ROOT}/pyproject.toml"


def _run_mypy(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mypy", *args],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )


def _read_pyproject() -> str:
    with open(PYPROJECT, encoding="utf-8") as fh:
        return fh.read()


def test_experiment_capabilities_service_passes_mypy_strict():
    """``server/services/experiment_capabilities_service.py`` passes strict."""
    result = _run_mypy("--strict", "server/services/experiment_capabilities_service.py")
    file_errors = [
        line for line in result.stdout.splitlines()
        if "experiment_capabilities_service.py:" in line and "error:" in line
    ]
    assert not file_errors, (
        f"found {len(file_errors)} error(s) in server-side mypy output:\n" + "\n".join(file_errors) +
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_pyproject_promotes_all_seven_to_strict():
    """pyproject must list all 7 modules in one override block."""
    text = _read_pyproject()
    pattern = re.compile(
        r"\[\[tool\.mypy\.overrides\]\][^{]*?module\s*=\s*\[.*?"
        r"\"server\.api\.topics\".*?"
        r"\"server\.api\.experiments\".*?"
        r"\"server\.api\.projects\".*?"
        r"\"server\.api\.agents\".*?"
        r"\"server\.services\.project_service\".*?"
        r"\"server\.services\.phase_service\".*?"
        r"\"server\.services\.experiment_capabilities_service\".*?"
        r"\][^{]*?strict\s*=\s*true",
        re.DOTALL,
    )
    assert pattern.search(text), (
        "pyproject.toml must keep [[tool.mypy.overrides]] promoting "
        "all 7 modules to strict = true in a single module list"
    )


def test_baseline_mypy_clean_for_experiment_capabilities_service():
    """Sanity: with the pyproject config (which promotes this module to
    strict), running mypy on it alone is still clean.
    """
    result = _run_mypy("server/services/experiment_capabilities_service.py")
    file_errors = [
        line for line in result.stdout.splitlines()
        if "experiment_capabilities_service.py:" in line and "error:" in line
    ]
    file_errors_msg = "\n".join(file_errors)
    assert not file_errors, (
        f"mypy reported {len(file_errors)} error(s):\n"
        f"{file_errors_msg}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_experiment_capabilities_service_no_bare_dict_annotations():
    """Regression guards for the three PR5c fixes: no bare ``dict``
    annotations in the file (each must be ``dict[str, Any]`` or similar).
    """
    path = f"{PROJECT_ROOT}/server/services/experiment_capabilities_service.py"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    no_comments = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    bare = re.findall(r":\s*dict(?!\[)", no_comments)
    assert not bare, (
        f"server/services/experiment_capabilities_service.py must not "
        f"use bare ``dict`` annotations. Found {len(bare)} occurrences."
    )
