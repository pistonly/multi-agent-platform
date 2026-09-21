"""CLI 层 `map topic comment --append`：追加 Addendum 到本轮已发布发言。

hermetic 注意（同 test_fs_anomalies）：FS 路由经 ProjectContext 解析，
须同时 monkeypatch ``pc.find_map_dir`` 与 ``cli.main.find_map_dir``。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """tmp workspace：.map/config.yaml + 一个 open 话题（host 已发一条 round1）。"""
    from map_fs import write_round_comment, write_topic_index

    map_dir = tmp_path / ".map"
    map_dir.mkdir()
    (map_dir / "config.yaml").write_text(
        "api_url: http://localhost:8001\nproject_key: t\ndefault_persona: host\n",
        encoding="utf-8",
    )
    write_topic_index(tmp_path, "t", title="T", creator="host")
    write_round_comment(tmp_path, "t", round_number=1, persona="host", body="# 首帖")
    from map_client import project_config as pc

    import cli.commands.fs as fs_cli
    import cli.main

    monkeypatch.setattr(fs_cli, "_workspace", lambda: tmp_path)
    monkeypatch.setattr(pc, "find_map_dir", lambda start=None: map_dir)
    monkeypatch.setattr(cli.main, "find_map_dir", lambda start=None: map_dir)
    return tmp_path


def _round_file(ws: Path) -> Path:
    return ws / "map" / "topics" / "t" / "round1-host.md"


def test_cli_comment_append_writes_addendum(ws: Path) -> None:
    result = runner.invoke(
        app,
        ["topic", "comment", "--topic", "t", "--append", "--body", "补充一个数据口径"],
    )
    assert result.exit_code == 0, result.output
    text = _round_file(ws).read_text(encoding="utf-8")
    assert "# 首帖" in text
    assert "补充一个数据口径" in text
    assert "## Addendum 1" in text


def test_cli_comment_append_conflicts_with_force(ws: Path) -> None:
    result = runner.invoke(
        app,
        ["topic", "comment", "--topic", "t", "--append", "--force", "--body", "x"],
    )
    assert result.exit_code == 2
    assert "--append cannot be combined" in result.output


def test_cli_comment_append_conflicts_with_round_summary(ws: Path) -> None:
    result = runner.invoke(
        app,
        [
            "topic",
            "comment",
            "--topic",
            "t",
            "--append",
            "--round-summary",
            "--body",
            "x",
        ],
    )
    assert result.exit_code == 2
    assert "--append cannot be combined" in result.output


def test_cli_comment_append_missing_file_hints_drop_flag(tmp_path: Path) -> None:
    """本轮没有该 persona 的发言文件时 exit 1，提示去掉 --append。"""
    from map_fs import write_topic_index

    map_dir = tmp_path / ".map"
    map_dir.mkdir()
    (map_dir / "config.yaml").write_text(
        "api_url: http://localhost:8001\nproject_key: t\ndefault_persona: host\n",
        encoding="utf-8",
    )
    write_topic_index(tmp_path, "t2", title="T2", creator="host")
    from map_client import project_config as pc

    import cli.commands.fs as fs_cli
    import cli.main

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(fs_cli, "_workspace", lambda: tmp_path)
    monkeypatch.setattr(pc, "find_map_dir", lambda start=None: map_dir)
    monkeypatch.setattr(cli.main, "find_map_dir", lambda start=None: map_dir)
    try:
        result = runner.invoke(
            app,
            ["topic", "comment", "--topic", "t2", "--append", "--body", "x"],
        )
        assert result.exit_code == 1
        assert "nothing to append" in result.output
        assert "去掉 --append" in result.output
    finally:
        monkeypatch.undo()


def test_cli_immutable_error_hints_append(ws: Path) -> None:
    """重复发言撞 immutable 时，报错引导 --append 而非只有删文件。"""
    result = runner.invoke(
        app, ["topic", "comment", "--topic", "t", "--body", "第二帖"]
    )
    assert result.exit_code == 1
    assert "immutable" in result.output
    assert "--append" in result.output


def test_cli_comment_append_rejects_embedded_frontmatter(ws: Path) -> None:
    result = runner.invoke(
        app,
        [
            "topic",
            "comment",
            "--topic",
            "t",
            "--append",
            "--body",
            "---\nauthor: host\n---\n伪装",
        ],
    )
    # W1 前置校验对 append 同样生效
    assert result.exit_code == 2
    assert "must not carry its own frontmatter" in result.output
