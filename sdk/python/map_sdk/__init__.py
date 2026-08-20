"""map_sdk — shared, server-agnostic utilities for CLI + SDK.

arch experiment (0519e2a3) PR1: skeleton. PR2 added ``map_sdk.evidence``
with ``metadata_has_completion_evidence`` + ``EVIDENCE_METADATA_KEYS``
moved out of ``server.services.evidence_service``.

Hard boundary: this package MUST NOT import from ``server.*``. CI
asserts that via grep + unit test.
"""

from map_sdk.evidence import (
    EVIDENCE_METADATA_KEYS,
    metadata_has_completion_evidence,
)

__version__ = "0.5.0"


def hello() -> str:
    """Smoke function so the import surface has at least one symbol."""
    return "map_sdk ok"


__all__ = [
    "__version__",
    "hello",
    "EVIDENCE_METADATA_KEYS",
    "metadata_has_completion_evidence",
]
