"""F5 (v0.13 M59b-4): lint gate coverage regression lock.

alembic/ and test_project/ once sat outside the ruff gate — not because of
an explicit exclude, but because the gate line simply never listed them
(173 errors accumulated unnoticed; see docs/prd/v0.13.md §M59). This test
pins the CI workflow ruff lines so the directories cannot silently slip
out of the gate again.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

_WORKFLOWS = (".github/workflows/ci.yml", ".github/workflows/nightly.yml")
_REQUIRED_DIRS = ("alembic", "test_project")


def _ruff_run_lines(workflow_text: str) -> list[str]:
    """Extract every ``run: ruff check ...`` line from a workflow file."""
    lines: list[str] = []
    for line in workflow_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("run:") and "ruff check" in stripped:
            lines.append(stripped)
    return lines


def test_ruff_gate_covers_blind_spot_dirs() -> None:
    for workflow in _WORKFLOWS:
        path = _REPO_ROOT / workflow
        assert path.is_file(), f"workflow {workflow} not found"
        run_lines = _ruff_run_lines(path.read_text(encoding="utf-8"))
        assert run_lines, (
            f"{workflow} has no `run: ruff check ...` line — the lint gate "
            "moved or was renamed; update this lock test to follow it"
        )
        for required in _REQUIRED_DIRS:
            # Match the directory as a whole word token on the run line so
            # ``alembic`` does not match e.g. a hypothetical ``alembic_x``.
            assert any(re.search(rf"\b{required}\b", ln) for ln in run_lines), (
                f"{workflow}'s ruff check line no longer covers `{required}` — "
                "that directory once accumulated 100+ lint errors while "
                "outside the gate (v0.13 F5); re-add it or record a "
                "deliberate exclusion decision in docs/prd."
            )
