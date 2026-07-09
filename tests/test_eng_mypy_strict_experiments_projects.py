"""Tests for eng experiment (55634575) PR3 — promote experiments.py +
projects.py to mypy strict alongside topics.py.

PR3 expands the strict surface from 1 directory to 3:

* ``server.api.topics`` (PR2 baseline)
* ``server.api.experiments`` (PR3, no pre-existing gaps)
* ``server.api.projects`` (PR3, fixed 1 return-type gap)

It also fixes pre-existing ``dict`` type-arg gaps in ``server/api/common.py``
(``dict`` → ``dict[str, Any]``) that block promoting projects.py to strict
(common.py is imported transitively).

Pins:
1. ``server/api/experiments.py`` passes ``mypy --strict`` — primary
   acceptance for the experiments module.
2. ``server/api/projects.py`` passes ``mypy --strict`` — primary
   acceptance for the projects module.
3. ``pyproject.toml`` lists all 3 modules in the strict override.
4. ``server/api/common.py`` no longer has any naked ``dict`` (or ``dict | None``)
   annotation (the regression that PR3 is closing).
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


def test_experiments_py_passes_mypy_strict():
    """``server/api/experiments.py`` passes ``mypy --strict``.

    No pre-existing type gaps — should pass cleanly on first try.
    """
    result = _run_mypy("--strict", "server/api/experiments.py")
    file_errors = [
        line for line in result.stdout.splitlines()
        if "experiments.py:" in line and "error:" in line
    ]
    assert not file_errors, (
        f"found {len(file_errors)} error(s) in server-side mypy output:\n" + "\n".join(file_errors) +
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_projects_py_passes_mypy_strict():
    """``server/api/projects.py`` passes ``mypy --strict``.

    Pre-existing gap was fixed in this PR: ``revise_project_status`` now
    returns ``ProjectStatusVersionRead.model_validate(version)`` instead
    of the raw ``ProjectStatusVersion`` ORM model.
    """
    result = _run_mypy("--strict", "server/api/projects.py")
    file_errors = [
        line for line in result.stdout.splitlines()
        if "experiments.py:" in line and "error:" in line
    ]
    file_errors_msg = "\n".join(file_errors)
    assert not file_errors, (
        f"mypy reported {len(file_errors)} error(s):\n"
        f"{file_errors_msg}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_pyproject_promotes_all_three_to_strict():
    """pyproject must list topics + experiments + projects in one override
    block under ``strict = true``. This catches accidental removal in
    future pyproject edits.

    The module list can be inline (``[a, b, c]``) or multi-line
    (``[\n  a,\n  b,\n  c\n]``) — the regex uses ``.*?`` with ``DOTALL`` to
    accept either.
    """
    text = _read_pyproject()
    pattern = re.compile(
        r"\[\[tool\.mypy\.overrides\]\][^{]*?module\s*=\s*\[.*?"
        r"\"server\.api\.topics\".*?"
        r"\"server\.api\.experiments\".*?"
        r"\"server\.api\.projects\".*?"
        r"\][^{]*?strict\s*=\s*true",
        re.DOTALL,
    )
    assert pattern.search(text), (
        "pyproject.toml must keep [[tool.mypy.overrides]] promoting "
        "topics + experiments + projects to strict = true in a single "
        "module list (inline or multi-line)"
    )


def test_common_py_has_no_naked_dict_annotations():
    """Regression guard: PR3 closed pre-existing ``dict`` (without type args)
    gaps in ``server/api/common.py`` because they block promoting
    projects.py to strict (common.py is imported transitively).

    Search for ``: dict`` (parameter annotation with bare ``dict``)
    outside of comments and string literals.
    """
    common_path = f"{PROJECT_ROOT}/server/api/common.py"
    with open(common_path, encoding="utf-8") as fh:
        text = fh.read()

    # Strip line comments to avoid false positives.
    no_comments = "\n".join(line.split("#", 1)[0] for line in text.splitlines())

    # ``dict`` (no type args) is what mypy strict rejects under
    # ``disallow_untyped_defs`` + ``no-untyped-def``. ``dict[str, Any]`` and
    # ``dict[str, Any] | None`` are fine.
    bare = re.findall(r":\s*dict(?!\[)", no_comments)
    assert not bare, (
        f"server/api/common.py must not use bare ``dict`` annotations "
        f"(use ``dict[str, Any]`` instead). Found {len(bare)} occurrences."
    )


def test_baseline_mypy_clean_for_all_three_modules():
    """Sanity: with the pyproject config (which promotes all 3 modules
    to strict), running mypy on each module alone is still clean. Protects
    against accidental demotion in a future pyproject edit.
    """
    for module in ("server/api/topics.py", "server/api/experiments.py", "server/api/projects.py"):
        result = _run_mypy(module)
        target_basename = module.split("/")[-1]
        file_errors = [
            line for line in result.stdout.splitlines()
            if target_basename in line and "error:" in line
        ]
        file_errors_msg = "\n".join(file_errors)
        assert not file_errors, (
            f"mypy reported {len(file_errors)} error(s) for {module}:\n"
            f"{file_errors_msg}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
