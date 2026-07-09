"""Tests for eng experiment (55634575) PR5d — promote
server/services/mention_service.py to mypy strict.

Fourth services-layer module on the strict surface (after
project_service.py / phase_service.py / experiment_capabilities_service.py).

Pre-existing gaps fixed:

* Line 357 — ``audit_payload_extra: dict | None`` → ``dict[str, Any] | None``
* Lines 440 + 462 — two parent-walking loops
  (``_experiment_thread_comment_ids`` / ``_topic_thread_comment_ids``)
  where mypy could not narrow ``cur`` (assigned from
  ``by_id[cur]`` → ``UUID | None``) across iterations. Fixed with
  ``while True`` + ``parent = by_id[cur]; if parent is None: break``
  pattern (the same pattern already used in ``thread_activity.thread_root_id``).
* Bonus cleanup — ``_walk_root`` had a pre-existing
  ``# type: ignore[assignment]`` for the same narrowing issue. PR5d
  refactored it to the same pattern and removed the ``# type: ignore``
  (it would otherwise become an unused-ignore warning under the
  project-wide ``warn_unused_ignores = true`` flag).

Pins:
1. ``server/services/mention_service.py`` passes ``mypy --strict``.
2. ``pyproject.toml`` lists all 8 modules in one override block.
3. Baseline mypy (project config) clean.
4. No unused ``# type: ignore`` comments remain in the file.
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


def test_mention_service_passes_mypy_strict():
    """``server/services/mention_service.py`` passes strict."""
    result = _run_mypy("--strict", "server/services/mention_service.py")
    file_errors = [
        line for line in result.stdout.splitlines()
        if "mention_service.py:" in line and "error:" in line
    ]
    assert not file_errors, (
        f"found {len(file_errors)} error(s) in server-side mypy output:\n" + "\n".join(file_errors) +
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_pyproject_promotes_all_eight_to_strict():
    """pyproject must list all 8 modules in one override block."""
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
        r"\"server\.services\.mention_service\".*?"
        r"\][^{]*?strict\s*=\s*true",
        re.DOTALL,
    )
    assert pattern.search(text), (
        "pyproject.toml must keep [[tool.mypy.overrides]] promoting "
        "all 8 modules to strict = true in a single module list"
    )


def test_baseline_mypy_clean_for_mention_service():
    """Sanity: with the pyproject config (which promotes mention_service
    to strict), running mypy on it alone is still clean.
    """
    result = _run_mypy("server/services/mention_service.py")
    file_errors = [
        line for line in result.stdout.splitlines()
        if "mention_service.py:" in line and "error:" in line
    ]
    file_errors_msg = "\n".join(file_errors)
    assert not file_errors, (
        f"mypy reported {len(file_errors)} error(s):\n"
        f"{file_errors_msg}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_mention_service_no_bare_dict_or_type_ignore():
    """Regression guards:
    * No bare ``dict`` annotations.
    * No ``# type: ignore`` comments remain (the lone one in
      ``_walk_root`` was refactored away in this PR).
    """
    path = f"{PROJECT_ROOT}/server/services/mention_service.py"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    no_comments = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    bare = re.findall(r":\s*dict(?!\[)", no_comments)
    assert not bare, (
        f"server/services/mention_service.py must not use bare ``dict`` "
        f"annotations. Found {len(bare)} occurrences."
    )
    # Whole-file scan (including comments) for type: ignore — should be
    # gone after PR5d refactored the last one away.
    assert "# type: ignore" not in text, (
        "server/services/mention_service.py must not contain "
        "``# type: ignore`` comments (PR5d removed the last one)."
    )
