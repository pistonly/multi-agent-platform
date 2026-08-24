"""map work --kinds / --explain 单测（实验 d559f431 I2，方向 A 过渡）。

直接测 ``_print_work_kinds``（不经 CliRunner，避开 CLI envelope 族的
stdout 形态耦合）。
"""

from __future__ import annotations

import pytest
import typer

from cli.main import _print_work_kinds
from server.services.work_kinds import WORK_ITEM_KINDS


def test_print_work_kinds_lists_all(capsys) -> None:
    _print_work_kinds(None)
    out = capsys.readouterr().out
    for spec in WORK_ITEM_KINDS:
        assert f"kind: {spec.kind}" in out
        assert spec.skill in out
        assert spec.clear_action in out


def test_print_work_kinds_explain_single(capsys) -> None:
    _print_work_kinds("mentions")
    out = capsys.readouterr().out
    assert "kind: mentions" in out
    assert out.count("kind: ") == 1  # 只输出单条


def test_print_work_kinds_explain_miss_exits_2(capsys) -> None:
    with pytest.raises(typer.Exit) as exc:
        _print_work_kinds("no_such_kind")
    assert exc.value.exit_code == 2
    err = capsys.readouterr().err
    assert "no_such_kind" in err
