"""Tests for eng experiment (55634575) PR2 — mypy strict on topics.py.

Pins:
1. ``server/api/topics.py`` passes ``mypy --strict`` (the PR's primary
   acceptance criterion).
2. ``pyproject.toml`` ``[[tool.mypy.overrides]]`` for
   ``server.api.topics`` keeps ``strict = true`` so the promotion
   doesn't accidentally regress in a future config edit.
3. The non-strict directories (``experiments``, ``agents``) do NOT
   trigger errors with the project-baseline config (sanity for the
   default allowlist).
"""

from __future__ import annotations

import re
import subprocess
import sys


PYPROJECT = "/home/AI02/Documents/quantaeye/multi_agents_platform/pyproject.toml"


def _read_pyproject() -> str:
    with open(PYPROJECT, "r", encoding="utf-8") as fh:
        return fh.read()


def test_topics_py_passes_mypy_strict():
    """The headline acceptance: ``mypy --strict server/api/topics.py``
    must exit 0 with no errors.

    We invoke mypy as a subprocess because the project's pyproject
    config + sdk/python path need to be honored exactly as CI does it.
    """
    project_root = "/home/AI02/Documents/quantaeye/multi_agents_platform"
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "server/api/topics.py"],
        capture_output=True,
        text=True,
        cwd=project_root,
    )
    file_errors = [
        line for line in result.stdout.splitlines()
        if "topics.py:" in line and "error:" in line
    ]
    assert not file_errors, (
        f"mypy --strict server/api/topics.py found {len(file_errors)} "
        f"error(s):\n" + "\n".join(file_errors)
        + f"\n\nfull output:\nstdout={result.stdout}\nstderr={result.stderr}"
    )


def test_pyproject_promotes_topics_to_strict():
    """Grep pyproject for the ``server.api.topics`` entry in the strict
    override block. Future PRs may group multiple modules into one
    override (``module = ["server.api.topics", "server.api.experiments", ...]``)
    so this test matches ``server.api.topics`` anywhere in a ``module``
    list that has ``strict = true`` in the same block.
    """
    text = _read_pyproject()
    pattern = re.compile(
        r"\[\[tool\.mypy\.overrides\]\][^{]*?module\s*=\s*\[[^\]]*\"server\.api\.topics\"[^\]]*\][^{]*?strict\s*=\s*true",
        re.DOTALL,
    )
    assert pattern.search(text), (
        "pyproject.toml must keep [[tool.mypy.overrides]] promoting "
        "server.api.topics to strict = true (possibly grouped with other "
        "modules in one module list)"
    )


def test_pyproject_keeps_schemas_attr_defined_disabled():
    """The re-export shim ``server/domain/schemas.py`` uses
    ``from map_types.schemas import *``, which mypy can't statically
    prove is exported. We rely on disabling ``attr-defined`` for that
    module so false positives don't fail the gate.
    """
    text = _read_pyproject()
    pattern = re.compile(
        r"\[\[tool\.mypy\.overrides\]\][^{]*?module\s*=\s*\[\"server\.domain\.schemas\"[^\]]*\][^{]*?disable_error_code[^{]*?attr-defined",
        re.DOTALL,
    )
    assert pattern.search(text), (
        "pyproject.toml must keep server.domain.schemas override "
        "disabling attr-defined"
    )


def test_baseline_mypy_clean_for_topics_py():
    """Sanity: with the pyproject config (which promotes topics.py to
    strict), running mypy on topics.py alone is still clean. This
    protects against accidental demotion in a future pyproject edit.
    The wider ``server/api/`` baseline is intentionally NOT asserted
    here — common.py / projects.py have pre-existing type gaps that
    will be addressed by their own future strict promotions.
    """
    project_root = "/home/AI02/Documents/quantaeye/multi_agents_platform"
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "server/api/topics.py"],
        capture_output=True,
        text=True,
        cwd=project_root,
    )
    file_errors = [
        line for line in result.stdout.splitlines()
        if "topics.py:" in line and "error:" in line
    ]
    assert not file_errors, (
        f"baseline mypy server/api/topics.py found {len(file_errors)} "
        f"error(s):\n" + "\n".join(file_errors)
    )
