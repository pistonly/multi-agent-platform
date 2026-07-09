"""map_sdk — shared, server-agnostic utilities for CLI + SDK.

arch experiment (0519e2a3) PR1: skeleton. Later PRs move shared
helpers here (see plan: ``metadata_has_completion_evidence`` etc.).
Hard boundary: this package MUST NOT import from ``server.*``. CI
asserts that via grep + unit test.
"""

__version__ = "0.1.0"


def hello() -> str:
    """Smoke function so the import surface has at least one symbol."""
    return "map_sdk ok"


__all__ = ["__version__", "hello"]
