"""Tests for arch experiment (0519e2a3) PR2 — map_sdk.evidence module.

Verifies:

1. ``map_sdk.evidence.metadata_has_completion_evidence`` is the same
   function object that ``server.services.evidence_service`` re-exports
   (no behavioral drift).
2. The map_sdk package does NOT import from ``server.*`` (CI grep gate).
3. The semantic cases from the original helper still pass.
"""

from __future__ import annotations

import subprocess


def test_map_sdk_does_not_import_server():
    """arch plan §7 CI gate: ``map_sdk`` MUST NOT ``import server`` or
    ``from server import ...``. We assert programmatically so a future
    PR regressing the boundary fails loudly.

    Only ``.py`` source files are scanned; docstrings that mention the
    literal phrase "from server" (e.g. in this gate's own description)
    are excluded by requiring the line to start with the import
    keyword after optional whitespace.
    """
    import re

    sdk_root = "sdk/python/map_sdk"
    forbidden = re.compile(r"^\s*(?:from\s+server\b|import\s+server\b)")
    offenders: list[str] = []
    for path in __import__("pathlib").Path(sdk_root).rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if forbidden.match(line):
                offenders.append(f"{path}:{lineno}: {line}")
    assert not offenders, (
        f"map_sdk must not import from server.*; offenders:\n"
        + "\n".join(offenders)
    )


def test_map_sdk_evidence_re_exports_match_server_module():
    """The SDK copy must be the same callable as the server re-export."""
    from map_sdk.evidence import (
        EVIDENCE_METADATA_KEYS as sdk_keys,
        metadata_has_completion_evidence as sdk_helper,
    )
    from server.services.evidence_service import (
        EVIDENCE_METADATA_KEYS as srv_keys,
        metadata_has_completion_evidence as srv_helper,
    )
    # Same function object — server re-exports the SDK one.
    assert sdk_helper is srv_helper
    assert sdk_keys is srv_keys


def test_metadata_has_completion_evidence_semantics():
    """Carry over the original behaviour: non-dict, allow_missing,
    EVIDENCE_METADATA_KEYS hits, evidence dict/list — all True/False
    branches from the original."""
    from map_sdk.evidence import metadata_has_completion_evidence

    assert metadata_has_completion_evidence(None) is False
    assert metadata_has_completion_evidence("string") is False
    assert metadata_has_completion_evidence([]) is False

    # allow_missing_evidence short-circuits to True
    assert metadata_has_completion_evidence({"allow_missing_evidence": True}) is True

    # EVIDENCE_METADATA_KEYS: any non-empty value counts
    assert metadata_has_completion_evidence({"pytest_summary": "3/3 pass"}) is True
    assert metadata_has_completion_evidence({"pytest_summary": ""}) is False
    assert metadata_has_completion_evidence({"pytest_summary": None}) is False
    assert metadata_has_completion_evidence({"pytest_summary": []}) is False
    assert metadata_has_completion_evidence({"pytest_summary": {}}) is False

    # evidence dict: at least one non-empty value
    assert metadata_has_completion_evidence({"evidence": {"a": "v"}}) is True
    assert metadata_has_completion_evidence({"evidence": {"a": None}}) is False

    # evidence list: at least one entry
    assert metadata_has_completion_evidence({"evidence": ["x"]}) is True
    assert metadata_has_completion_evidence({"evidence": []}) is False

    # Empty metadata dict → False
    assert metadata_has_completion_evidence({}) is False


def test_map_sdk_package_exposes_evidence_symbols():
    """``import map_sdk`` re-exports the evidence helpers at top level
    so CLI callers can do ``from map_sdk import ...``."""
    import map_sdk

    assert hasattr(map_sdk, "metadata_has_completion_evidence")
    assert hasattr(map_sdk, "EVIDENCE_METADATA_KEYS")
    assert "metadata_has_completion_evidence" in map_sdk.__all__
    assert "EVIDENCE_METADATA_KEYS" in map_sdk.__all__
