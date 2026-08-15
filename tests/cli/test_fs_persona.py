"""fs 子命令 persona 解析：全局 map --persona 应透传到 fs 离线命令。

回归背景：``map --persona participant fs comment ...`` 曾回退到 config
default_persona（host），Agent 必须记住在 fs 子命令上重复传 --persona。
期望优先级：子命令显式 --persona > 全局 --persona > config 默认。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.main import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def fs_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """tmp workspace：.map/config.yaml（default_persona=host）+ 一个 open 话题。"""
    from map_fs import write_topic_index

    map_dir = tmp_path / ".map"
    map_dir.mkdir()
    (map_dir / "config.yaml").write_text(
        "api_url: http://localhost:8001\nproject_key: t\ndefault_persona: host\n",
        encoding="utf-8",
    )
    write_topic_index(tmp_path, "t", title="T", creator="host")
    import cli.commands.fs as fs_cli

    monkeypatch.setattr(fs_cli, "_workspace", lambda: tmp_path)
    return tmp_path


def _invoke_comment(runner: CliRunner, workspace: Path, *extra: str) -> None:
    src = workspace / "opinion.md"
    src.write_text("# 我的观点\n正文", encoding="utf-8")
    result = runner.invoke(app, ["fs", "comment", "--topic", "t", "--file", str(src), *extra])
    assert result.exit_code == 0, result.output


def test_global_persona_propagates_to_fs_comment(runner: CliRunner, fs_workspace: Path) -> None:
    src = fs_workspace / "opinion.md"
    src.write_text("# 观点", encoding="utf-8")
    result = runner.invoke(
        app,
        ["--persona", "participant", "fs", "comment", "--topic", "t", "--file", str(src)],
    )
    assert result.exit_code == 0, result.output
    assert (fs_workspace / "map" / "topics" / "t" / "round1-participant.md").is_file()


def test_subcommand_persona_overrides_global(runner: CliRunner, fs_workspace: Path) -> None:
    src = fs_workspace / "opinion.md"
    src.write_text("# 观点", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "--persona",
            "participant",
            "fs",
            "comment",
            "--persona",
            "reviewer",
            "--topic",
            "t",
            "--file",
            str(src),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (fs_workspace / "map" / "topics" / "t" / "round1-reviewer.md").is_file()


def test_falls_back_to_config_default(runner: CliRunner, fs_workspace: Path) -> None:
    _invoke_comment(runner, fs_workspace)
    assert (fs_workspace / "map" / "topics" / "t" / "round1-host.md").is_file()
