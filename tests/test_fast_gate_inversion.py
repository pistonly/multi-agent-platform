"""Tests for experiment eb291c4b — fast-gate allowlist inversion.

After inversion, deselection is driven solely by in-file markers
(``slow`` / ``integration`` / ``claude_cli``); the old opt-in
``_FAST_GATE_MODULES`` whitelist in ``tests/conftest.py`` and its
self-consistency guard (``test_eng_fast_gate_whitelist_complete``) are
gone.  These tests pin the post-inversion collection semantics:

1. A module with an explicit ``slow`` marker is deselected by the
   default gate (``-m "not slow and not integration and not claude_cli"``).
2. A module WITHOUT any marker is collected by the default gate — the
   "new file is silently deselected (fake-green)" failure mode is gone.
3. Nothing rescues modules via an opt-in whitelist anymore: the
   ``_FAST_GATE_MODULES`` name is absent from ``tests/conftest.py``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
TESTS_DIR = f"{PROJECT_ROOT}/tests"
CONFTEST = f"{TESTS_DIR}/conftest.py"

# A real module that carries an explicit slow marker today.
_SLOW_MODULE = "test_fs_scan_plane_perf_baseline.py"
# A real module that carries an explicit integration marker today.
_INTEGRATION_MODULE = "test_git_checkpoint.py"
# A real unit module without any gating marker.
_FAST_MODULE = "test_persona_identity_unified.py"

# A fixture directory pair: one marked slow, one unmarked — created under
# tmp_path so the test never depends on files that CI could prune.
_STUB_SLOW = "stub_slow.py"
_STUB_PLAIN = "stub_plain.py"


def _collect(*paths: str, marker_expr: str | None = None) -> list[str]:
    cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q", *paths]
    if marker_expr:
        cmd = [sys.executable, "-m", "pytest", "-m", marker_expr, "--collect-only", "-q", *paths]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
    return proc.stdout.splitlines()


def test_default_gate_collects_unmarked_module():
    """An unmarked module must be collected by the default gate.

    Pre-inversion this module was NOT in ``_FAST_GATE_MODULES`` and would
    silently deselect (fake green); post-inversion it must show up.
    """
    collected = _collect(f"{TESTS_DIR}/{_FAST_MODULE}")
    assert any(_FAST_MODULE in line for line in collected), collected


def test_default_gate_deselects_slow_marked_module():
    """An explicit ``slow`` marker module must be deselected by default gate.

    Only in-file markers drive deselection after inversion.
    """
    collected = _collect(
        f"{TESTS_DIR}/{_SLOW_MODULE}",
        marker_expr="not slow and not integration and not claude_cli",
    )
    assert not any(_SLOW_MODULE in line for line in collected), collected


def test_default_gate_deselects_integration_marked_module():
    """An explicit ``integration`` marker module is deselected by default gate."""
    collected = _collect(
        f"{TESTS_DIR}/{_INTEGRATION_MODULE}",
        marker_expr="not slow and not integration and not claude_cli",
    )
    assert not any(_INTEGRATION_MODULE in line for line in collected), collected


def test_stub_unmarked_is_collected(tmp_path):
    """Regression pin using self-made files: unmarked new file = collected.

    This is A2's persistent assertion, expressed through an ephemeral
    directory so it cannot drift with the real suite.
    """
    (tmp_path / _STUB_PLAIN).write_text("def test_plain_ok():\n    assert True\n", encoding="utf-8")
    (tmp_path / _STUB_SLOW).write_text(
        "import pytest\n\n"
        "@pytest.mark.slow\n"
        "def test_slow_off():\n    assert True\n",
        encoding="utf-8",
    )
    collected = _collect(
        f"{tmp_path}/{_STUB_PLAIN}",
        f"{tmp_path}/{_STUB_SLOW}",
        marker_expr="not slow and not integration and not claude_cli",
    )
    collected_items = [ln for ln in collected if "::" in ln]
    assert any(_STUB_PLAIN in line for line in collected_items), collected_items
    assert not any(_STUB_SLOW in line for line in collected_items), collected_items


def test_conftest_has_no_whitelist_residual():
    """The opt-in whitelist frozenset must not survive in conftest.py."""
    text = Path(CONFTEST).read_text(encoding="utf-8")
    assert "_FAST_GATE_MODULES" not in text
    assert "pytest_collection_modifyitems" not in text
