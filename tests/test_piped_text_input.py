"""CLI implicit stdin text-source contract."""

from __future__ import annotations

import io

import pytest
import typer

import cli.io_helpers as io_helpers


def test_read_piped_text_does_not_read_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    class TTYWithoutRead:
        def isatty(self) -> bool:
            return True

        def read(self, _size: int = -1) -> str:
            raise AssertionError("interactive stdin must not be read")

    monkeypatch.setattr(io_helpers.sys, "stdin", TTYWithoutRead())
    assert io_helpers._read_piped_text(kind="comment") is None


def test_read_piped_text_rejects_input_over_limit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        io_helpers.sys,
        "stdin",
        io.StringIO("x" * (io_helpers.MAX_PIPED_TEXT_CHARS + 1)),
    )

    with pytest.raises(typer.Exit) as exc_info:
        io_helpers._read_piped_text(kind="log")

    assert exc_info.value.exit_code == 2
    captured = capsys.readouterr()
    assert "exceeds" in captured.err
    assert "--file" in captured.err
