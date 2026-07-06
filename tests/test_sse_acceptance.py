"""Pytest wrappers for SSE acceptance scripts."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow

import importlib.util
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "tests" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_sse_isolation_passes():
    assert _load_script("test_sse_isolation.py").main() == 0


def test_sse_schema_overlap_passes():
    assert _load_script("test_sse_schema_overlap.py").main() == 0


def test_sse_isolation_detects_forbidden_import(tmp_path: Path):
    sse_isolation = _load_script("test_sse_isolation.py")
    bad_file = tmp_path / "bad_stream.py"
    bad_file.write_text(
        textwrap.dedent(
            """
            import server.services.webhook_service as webhook_service
            """
        ),
        encoding="utf-8",
    )
    violations = sse_isolation.forbidden_imports(bad_file)
    assert "server.services.webhook_service" in violations


def test_sse_schema_overlap_detects_extra_field(monkeypatch):
    sse_schema_overlap = _load_script("test_sse_schema_overlap.py")
    monkeypatch.setattr(
        sse_schema_overlap,
        "NOTIFICATION_CREATED_SSE_FIELDS",
        frozenset({"type", "event", "notification_id", "sse_only_field"}),
    )
    assert sse_schema_overlap.main() != 0
