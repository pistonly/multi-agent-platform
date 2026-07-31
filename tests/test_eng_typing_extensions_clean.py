"""Tests for eng experiment (55634575) PR6 — typing_extensions cleanup.

``pyproject`` declares ``>=3.10``. The source only uses stdlib
``typing`` constructs available in 3.10+ (``Protocol``, ``Literal``,
``TypedDict``, ``Final``, ``TypeVar``, ``Generic``, etc.).
This file pins that invariant so a future PR doesn't reintroduce
``typing_extensions`` imports into ``server/``, ``cli/``, ``sdk/``,
``map_client/``, ``map_sdk/`` without justification.

If a future change needs a ``typing`` construct not in 3.10 stdlib
(e.g. ``Self`` / ``assert_never`` / ``LiteralString`` which are 3.11+),
either bump ``requires-python`` or add a justified
``typing_extensions`` import and update this guard.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# 推断项目根目录：本测试位于 <root>/tests/ 下。
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)

# Runtime source roots — covers everything that ships or runs in dev/CI.
# Generated code under .map/ is excluded (those are plan/log artifacts).
SOURCE_ROOTS = [
    f"{PROJECT_ROOT}/server",
    f"{PROJECT_ROOT}/cli",
    f"{PROJECT_ROOT}/sdk",
    f"{PROJECT_ROOT}/map_client",
    f"{PROJECT_ROOT}/map_sdk",
    f"{PROJECT_ROOT}/map_types",
]

# Match any ``from typing_extensions import ...`` or
# ``import typing_extensions`` line in source files.
TYPING_EXT_RE = re.compile(r"^\s*(?:from\s+typing_extensions\s+import|import\s+typing_extensions)\b")


def _grep_typing_extensions() -> list[str]:
    """Return a list of ``path:line:content`` for any typing_extensions
    imports under the configured source roots.
    """
    matches: list[str] = []
    for root in SOURCE_ROOTS:
        result = subprocess.run(
            [
                "grep", "-rn",
                "-E", r"(from\s+typing_extensions\s+import|import\s+typing_extensions)",
                "--include=*.py",
                root,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            matches.append(line)
    return matches


def test_no_typing_extensions_imports_in_source():
    """No source file imports ``typing_extensions``.

    pyproject requires Python ``>=3.10`` — stdlib ``typing`` covers
    every construct the project actually uses (``Protocol``,
    ``Literal``, ``TypedDict``, ``Final``, ``TypeVar``,
    ``Generic``, etc.). If you need something not in 3.10 stdlib
    (e.g. ``Self`` / ``assert_never`` / ``LiteralString`` which are
    3.11+), justify ``typing_extensions`` in code review and update
    this guard.
    """
    hits = _grep_typing_extensions()
    assert not hits, (
        f"Found {len(hits)} typing_extensions import(s) under source roots "
        f"{SOURCE_ROOTS}. pyproject requires Python >=3.10 — stdlib "
        f"`typing` covers everything we use. Move the import to "
        f"stdlib `typing` or document the justification.\n\n"
        + "\n".join(hits)
    )


def test_pyproject_requires_python_310_or_above():
    """pyproject must continue to require Python 3.10+. If it drops
    below 3.10, the no-typing_extensions invariant above becomes
    load-bearing for ``Protocol`` / ``TypedDict`` / ``Literal`` etc.
    """
    pyproject = f"{PROJECT_ROOT}/pyproject.toml"
    with open(pyproject, encoding="utf-8") as fh:
        text = fh.read()
    match = re.search(r'requires-python\s*=\s*"([^"]+)"', text)
    assert match is not None, "pyproject.toml must declare requires-python"
    requires = match.group(1)
    # Match patterns like ">=3.10" or ">=3.10,<4.0"
    version_match = re.search(r">=\s*3\.(\d+)", requires)
    assert version_match is not None, (
        f"pyproject requires-python {requires!r} must include >=3.10 "
        f"for the no-typing_extensions invariant to hold"
    )
    minor = int(version_match.group(1))
    assert minor >= 10, (
        f"pyproject requires-python {requires!r} drops below 3.10 — "
        f"the no-typing_extensions guard is no longer safe."
    )
