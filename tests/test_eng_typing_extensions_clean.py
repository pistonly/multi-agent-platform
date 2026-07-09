"""Tests for eng experiment (55634575) PR6 — typing_extensions cleanup.

Per the topic-21812ad2 resolve plan: ``pyproject`` is ``>=3.11``,
which means stdlib ``typing`` covers everything we need
(``Protocol``, ``Literal``, ``TypedDict``, ``Final``, ``Self``,
``assert_never``, ``assert_type`` are all in 3.11+). This file
pins that invariant so a future PR doesn't reintroduce
``typing_extensions`` imports into ``server/``, ``cli/``, ``sdk/``,
``map_client/``, ``map_sdk/`` without justification.

The plan called for ``grep 'from typing_extensions' server/`` to be
zero — we extend the grep to all runtime source roots.
"""

from __future__ import annotations

import re
import subprocess

PROJECT_ROOT = "/home/AI02/Documents/quantaeye/multi_agents_platform"

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

    pyproject requires Python ``>=3.11`` — stdlib ``typing`` covers
    every construct the project actually uses (``Protocol``,
    ``Literal``, ``TypedDict``, ``Final``, ``Self``, ``assert_never``,
    ``assert_type``, ``TypeVar``, ``Generic``, etc.). If you need
    something not in 3.11 stdlib, justify ``typing_extensions`` in
    code review and update this guard.
    """
    hits = _grep_typing_extensions()
    assert not hits, (
        f"Found {len(hits)} typing_extensions import(s) under source roots "
        f"{SOURCE_ROOTS}. pyproject requires Python >=3.11 — stdlib "
        f"`typing` covers everything we use. Move the import to "
        f"stdlib `typing` or document the justification.\n\n"
        + "\n".join(hits)
    )


def test_pyproject_requires_python_311_or_above():
    """pyproject must continue to require Python 3.11+. If it drops
    below 3.11, the no-typing_extensions invariant above becomes
    load-bearing for ``Self`` / ``assert_never`` / etc.
    """
    pyproject = f"{PROJECT_ROOT}/pyproject.toml"
    with open(pyproject, encoding="utf-8") as fh:
        text = fh.read()
    match = re.search(r'requires-python\s*=\s*"([^"]+)"', text)
    assert match is not None, "pyproject.toml must declare requires-python"
    requires = match.group(1)
    # Match patterns like ">=3.11" or ">=3.11,<4.0"
    version_match = re.search(r">=\s*3\.(\d+)", requires)
    assert version_match is not None, (
        f"pyproject requires-python {requires!r} must include >=3.11 "
        f"for the no-typing_extensions invariant to hold"
    )
    minor = int(version_match.group(1))
    assert minor >= 11, (
        f"pyproject requires-python {requires!r} drops below 3.11 — "
        f"the no-typing_extensions guard is no longer safe."
    )
