"""Tests for eng experiment (55634575) PR5b — promote
server/services/phase_service.py to mypy strict.

Second services-layer module to join the strict surface (after
project_service.py in PR5). Two pre-existing ``dict`` type-arg gaps
fixed:

* Line 202 — ``_legacy_accept_result_metadata(payload_metadata: dict | None)``
  → ``dict[str, Any] | None`` (input annotation)
* Line 258 — ``cascaded: list[dict]`` → ``list[dict[str, Any]]``
  (matches the return type of ``topic_service._complete_action_item_no_commit``)

Pins:
1. ``server/services/phase_service.py`` passes ``mypy --strict``.
2. ``pyproject.toml`` lists all 4 server/api modules + project_service
   + phase_service in one override block under ``strict = true``.
3. Baseline mypy (project config) clean for phase_service.py.
4. Regression guards: ``dict | None`` and ``list[dict]`` are gone.
"""

from __future__ import annotations

import re
import subprocess
import sys

PYPROJECT = "/home/AI02/Documents/quantaeye/multi_agents_platform/pyproject.toml"
PROJECT_ROOT = "/home/AI02/Documents/quantaeye/multi_agents_platform"


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


def test_phase_service_py_passes_mypy_strict():
    """``server/services/phase_service.py`` passes ``mypy --strict``."""
    result = _run_mypy("--strict", "server/services/phase_service.py")
    file_errors = [
        line for line in result.stdout.splitlines()
        if "phase_service.py:" in line and "error:" in line
    ]
    assert not file_errors, (
        f"found {len(file_errors)} error(s) in server-side mypy output:\n" + "\n".join(file_errors) +
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_pyproject_promotes_all_six_to_strict():
    """pyproject must list all 6 modules in one override block."""
    text = _read_pyproject()
    pattern = re.compile(
        r"\[\[tool\.mypy\.overrides\]\][^{]*?module\s*=\s*\[.*?"
        r"\"server\.api\.topics\".*?"
        r"\"server\.api\.experiments\".*?"
        r"\"server\.api\.projects\".*?"
        r"\"server\.api\.agents\".*?"
        r"\"server\.services\.project_service\".*?"
        r"\"server\.services\.phase_service\".*?"
        r"\][^{]*?strict\s*=\s*true",
        re.DOTALL,
    )
    assert pattern.search(text), (
        "pyproject.toml must keep [[tool.mypy.overrides]] promoting "
        "topics + experiments + projects + agents + project_service + "
        "phase_service to strict = true in a single module list"
    )


def test_baseline_mypy_clean_for_phase_service_py():
    """Sanity: with the pyproject config (which promotes phase_service
    to strict), running mypy on it alone is still clean.
    """
    result = _run_mypy("server/services/phase_service.py")
    file_errors = [
        line for line in result.stdout.splitlines()
        if "phase_service.py:" in line and "error:" in line
    ]
    file_errors_msg = "\n".join(file_errors)
    assert not file_errors, (
        f"mypy reported {len(file_errors)} error(s):\n"
        f"{file_errors_msg}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_phase_service_no_bare_dict_or_list_dict_annotations():
    """Regression guards for the two PR5b fixes:

    * Line 202 input annotation: must NOT use bare ``dict | None``
      (must be ``dict[str, Any] | None``).
    * Line 258 cascaded annotation: must NOT use ``list[dict]``
      (must be ``list[dict[str, Any]]``).
    """
    path = f"{PROJECT_ROOT}/server/services/phase_service.py"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    no_comments = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    assert "dict | None" not in no_comments, (
        "server/services/phase_service.py must not use bare ``dict | None`` "
        "annotations (use ``dict[str, Any] | None`` instead)"
    )
    assert "list[dict]" not in no_comments, (
        "server/services/phase_service.py must not use ``list[dict]`` "
        "annotations (use ``list[dict[str, Any]]`` instead)"
    )
