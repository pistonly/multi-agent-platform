"""Tests for eng experiment (55634575) PR9 — fast-gate whitelist guard.

Regression guard: any ``tests/test_eng_*.py`` module MUST be listed in
``tests/conftest.py`` ``_FAST_GATE_MODULES`` so that
``pytest_collection_modifyitems`` doesn't auto-mark it as ``slow`` and
get silently deselected by ``-m 'not slow and not integration and not
claude_cli'``.

This was a load-bearing bug pre-PR9: 13 ``test_eng_mypy_strict_*.py``
modules + 2 guard tests (``test_eng_typing_extensions_clean``,
``test_eng_version_single_source``) were all silently deselected, and
prior log claims of "X/Y ✅" were false positives because the test
function bodies never ran.

The guard works by:

1. Discovering all ``tests/test_eng_*.py`` files on disk.
2. Parsing ``_FAST_GATE_MODULES`` out of ``tests/conftest.py``.
3. Asserting the two sets are equal. New test_eng files added without
   a whitelist entry trip this test.

Release workflow: when adding a new ``test_eng_*.py``, also add the
module name to ``_FAST_GATE_MODULES`` in ``tests/conftest.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = PROJECT_ROOT / "tests"
CONFTEST = TESTS_DIR / "conftest.py"

# Match the module names declared inside _FAST_GATE_MODULES.
_FAST_GATE_MODULE_RE = re.compile(r'"(test_[a-z0-9_]+)"')


def _discover_eng_test_modules() -> set[str]:
    """All ``tests/test_eng_*.py`` module names (without .py)."""
    return {
        p.stem
        for p in TESTS_DIR.glob("test_eng_*.py")
        if p.is_file()
    }


def _parse_fast_gate_modules() -> set[str]:
    """Module names declared in ``_FAST_GATE_MODULES`` set."""
    text = CONFTEST.read_text(encoding="utf-8")
    # Find the _FAST_GATE_MODULES = frozenset({ ... }) block
    match = re.search(
        r"_FAST_GATE_MODULES\s*=\s*(?:frozenset|set)\(\s*\{(.+?)\}\s*\)",
        text,
        re.DOTALL,
    )
    assert match is not None, (
        "Could not locate `_FAST_GATE_MODULES = { ... }` block in "
        f"{CONFTEST}. Update this guard if the structure changed."
    )
    body = match.group(1)
    return set(_FAST_GATE_MODULE_RE.findall(body))


def test_all_eng_test_modules_are_in_fast_gate():
    """Every ``tests/test_eng_*.py`` must be whitelisted.

    Without this, ``pytest_collection_modifyitems`` auto-marks the
    module ``slow`` and the default ``-m 'not slow'`` addopts silently
    deselects every test in it — making pass/fail claims meaningless.

    The whitelist also contains non-eng modules (``test_agent_client``
    etc.); the guard only asserts that the eng subset is a subset of
    the whitelist, not that the whitelist is eng-only.
    """
    on_disk = _discover_eng_test_modules()
    whitelisted = _parse_fast_gate_modules()
    missing = on_disk - whitelisted
    assert not missing, (
        f"Found {len(missing)} test_eng module(s) on disk that are NOT "
        f"in tests/conftest.py `_FAST_GATE_MODULES`:\n"
        + "\n".join(f"  - {m}" for m in sorted(missing))
        + "\n\nAdd them to the whitelist, otherwise they will be "
        "auto-marked slow and silently deselected by `-m 'not slow'`."
    )
