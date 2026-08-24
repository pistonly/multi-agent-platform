"""3b7c2b44 A6 P4-1 — ``topic create --help`` 不再泄漏 Ellipsis 占位符。

typer 0.16（PATH 上的 ``map``）对 ``Option(..., "--title")`` 会在 help 里渲染
``[default: <object object at 0x...>; required]``，泄漏 Python 对象地址。
改用 ``show_default=False`` 后只有 ``[required]``。本测试钉住关键行形态，
两版本（0.16 / 0.27）CliRunner 的输出应一致且无内存地址。
"""
from __future__ import annotations

from typer.testing import CliRunner

from cli.commands.topic import topic_app


def _help_snapshot() -> str:
    result = CliRunner().invoke(topic_app, ["create", "--help"])
    assert result.exit_code == 0
    return result.output


def test_title_option_no_placeholder() -> None:
    output = _help_snapshot()
    assert "<object object" not in output
    assert "0x" not in output
    assert "[default:" not in output
    assert "required" in output


def test_title_option_carries_help_text() -> None:
    output = _help_snapshot()
    assert "--title" in output
    assert "Map topic title." in output


def test_slug_option_still_documented() -> None:
    output = _help_snapshot()
    assert "--slug" in output
    assert "slugify(--title)" in output
