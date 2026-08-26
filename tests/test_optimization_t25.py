"""T25：inbound_event_record 复用 MapCommandClient._run，不再手抄 timeout/错误拼装。"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cli.errors import WorkerError
from cli.map_command_client import MapCommandClient


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_inbound_event_record_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: _completed(0, stdout="ok\n")
    )
    client = MapCommandClient(persona="host", cmd_timeout=5)
    assert client.inbound_event_record(
        event_id="e1", fingerprint="fp", event_type="mention"
    ) is True


def test_inbound_event_record_duplicate_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _completed(2, stderr="409"))
    client = MapCommandClient(persona="host", cmd_timeout=5)
    assert client.inbound_event_record(
        event_id="e1", fingerprint="fp", event_type="mention"
    ) is False


def test_inbound_event_record_other_exit_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _completed(1, stderr="boom"))
    client = MapCommandClient(persona="host", cmd_timeout=5)
    with pytest.raises(WorkerError, match="Command failed \\(1\\)"):
        client.inbound_event_record(event_id="e1", fingerprint="fp", event_type="mention")


def test_inbound_event_record_dry_run_skips_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    run = MagicMock(side_effect=AssertionError("dry-run must not spawn"))
    monkeypatch.setattr(subprocess, "run", run)
    client = MapCommandClient(persona="host", dry_run=True, cmd_timeout=5)
    assert client.inbound_event_record(
        event_id="e1", fingerprint="fp", event_type="mention"
    ) is True
    run.assert_not_called()
