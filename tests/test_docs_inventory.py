"""docs/ structural inventory (cleanup follow-up c9281d86 PR2).

PR2 reorganises the ``docs/`` tree so PRDs live under ``docs/prd/``:

* Current draft at ``docs/prd/v0.9.md``
* Historical PRDs at ``docs/prd/archive/v0.{1..6}.md``
* v0.7 / v0.8 are placeholder docs (no separate publication)
* ``docs/prd/README.md`` is the PRD entry point
* ``docs/INDEX.md`` is the top-level docs entry point

These tests guard the structural contracts so future drift gets caught
by ``pytest`` rather than a reviewer chasing 404s in a year.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"
PRD = DOCS / "prd"
PRD_ARCHIVE = PRD / "archive"


def test_prd_root_exists() -> None:
    assert PRD.is_dir(), f"{PRD} should be a directory"


def test_prd_archive_exists() -> None:
    assert PRD_ARCHIVE.is_dir(), f"{PRD_ARCHIVE} should be a directory"


@pytest.mark.parametrize(
    "version",
    ["v0.1", "v0.2", "v0.3", "v0.4", "v0.5", "v0.6"],
)
def test_historical_prds_archived(version: str) -> None:
    """Each historical PRD has a corresponding file in ``docs/prd/archive/``."""
    path = PRD_ARCHIVE / f"{version}.md"
    assert path.is_file(), f"missing archived PRD: {path}"
    # First line should be a markdown header (defensive against empty files).
    first_line = path.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("#"), f"{path} does not start with a header"


@pytest.mark.parametrize("version", ["v0.7", "v0.8"])
def test_v07_v08_placeholders_exist(version: str) -> None:
    """v0.7 / v0.8 are explicit placeholder docs (no separate publication)."""
    path = PRD_ARCHIVE / f"{version}.md"
    assert path.is_file(), f"missing placeholder PRD: {path}"
    body = path.read_text(encoding="utf-8")
    # Placeholder markers — must declare "未单独成稿" or "占位".
    assert ("占位" in body) or ("未单独成稿" in body), (
        f"{path} should explicitly declare it's a placeholder; "
        f"otherwise it's confusing why v0.7/v0.8 skip a draft number."
    )


def test_current_prd_v09_in_prd_root() -> None:
    path = PRD / "v0.9.md"
    assert path.is_file(), f"current draft should be at {path}"
    body = path.read_text(encoding="utf-8")
    assert "v0.9" in body, f"{path} should declare it's v0.9"


def test_prd_readme_is_entry_point() -> None:
    """``docs/prd/README.md`` is the canonical PRD index page."""
    path = PRD / "README.md"
    assert path.is_file(), f"PRD entry README missing: {path}"
    body = path.read_text(encoding="utf-8")
    # Should list archived versions and point to current draft.
    assert "[v0.9" in body or "./v0.9.md" in body, (
        f"{path} should link to current v0.9 draft"
    )
    assert "archive" in body, f"{path} should mention the archive subdirectory"


def test_top_level_docs_index_exists() -> None:
    """``docs/INDEX.md`` is the top-level navigation entry."""
    path = DOCS / "INDEX.md"
    assert path.is_file(), f"top-level docs index missing: {path}"
    body = path.read_text(encoding="utf-8")
    # Should reference current PRD + archive.
    assert "prd/v0.9.md" in body or "PRD v0.9" in body, (
        f"{path} should link to current PRD v0.9"
    )
    assert "ARCHITECTURE" in body, f"{path} should reference architecture doc"


def test_no_orphan_prd_at_docs_root() -> None:
    """No ``PRD*.md`` files should live directly under ``docs/`` — they
    must all live under ``docs/prd/`` (current) or ``docs/prd/archive/``
    (history). Catches accidental ``git mv`` regressions.
    """
    orphans = sorted(
        p.name for p in DOCS.glob("PRD*.md")
    )
    assert not orphans, (
        f"Found orphan PRD files at docs/ root: {orphans}. "
        f"Move them under docs/prd/ or docs/prd/archive/."
    )


def test_prd_internal_cross_refs_resolve() -> None:
    """Each PRD file's relative ``./v*.md`` references must point to an
    existing file under ``docs/prd/archive/``. Catches broken archive
    links after future moves.
    """
    for prd_file in PRD_ARCHIVE.glob("v*.md"):
        text = prd_file.read_text(encoding="utf-8")
        # Markdown link form: ``[text](./v0.X.md)`` (relative to this file).
        for ref in _extract_relative_md_links(text, prefix="./"):
            if ref.startswith("./") and ref.endswith(".md"):
                # Reference points to a sibling in archive/.
                target = PRD_ARCHIVE / ref[2:]
                assert target.is_file(), (
                    f"{prd_file.name} references missing {ref} "
                    f"(resolved to {target}, which does not exist)"
                )


def _extract_relative_md_links(text: str, prefix: str) -> list[str]:
    """Extract link targets from markdown ``[label](target)`` patterns
    that start with the given prefix.
    """
    import re

    pattern = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    return [m for m in (match.group(1) for match in pattern.finditer(text)) if m.startswith(prefix)]
