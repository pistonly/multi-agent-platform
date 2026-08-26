"""fs-write-entry-validation（27f961d1）CLI 层：anomaly 双出口 + W1 拒绝文案。"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """tmp workspace：.map/config.yaml + 一个 open 话题（含一条脏 round 文件）。"""
    from map_fs import write_round_comment, write_topic_index

    map_dir = tmp_path / ".map"
    map_dir.mkdir()
    (map_dir / "config.yaml").write_text(
        "api_url: http://localhost:8001\nproject_key: t\ndefault_persona: host\n",
        encoding="utf-8",
    )
    write_topic_index(tmp_path, "t", title="T", creator="host")
    write_round_comment(tmp_path, "t", round_number=1, persona="host", body="# r1")
    # 脏文件：posted_at 非法占位符（E1 锚点同形态）
    d = tmp_path / "map" / "topics" / "t"
    (d / "round1-participant.md").write_text(
        "---\nauthor: participant\nround: 1\nposted_at: '$ts'\n---\n# dirty\n",
        encoding="utf-8",
    )
    import cli.commands.fs as fs_cli
    import cli.commands.topic as topic_cli

    monkeypatch.setattr(fs_cli, "_workspace", lambda: tmp_path)
    monkeypatch.setattr(topic_cli, "_optional_workspace", lambda: tmp_path)
    return tmp_path


def test_fs_anomalies_lists_dirty_file(ws: Path) -> None:
    result = runner.invoke(app, ["topic", "anomalies"])
    assert result.exit_code == 0, result.output
    assert "round1-participant.md" in result.output
    assert "invalid" in result.output
    assert "posted_at missing or unparseable" in result.output


def test_fs_anomalies_json_format(ws: Path) -> None:
    import json

    result = runner.invoke(app, ["topic", "anomalies", "--format", "json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert rows and rows[0]["level"] == "invalid" and rows[0]["topic"] == "t"


def test_fs_anomalies_empty_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from map_fs import write_topic_index

    (tmp_path / ".map").mkdir()
    (tmp_path / ".map" / "config.yaml").write_text(
        "api_url: http://localhost:8001\nproject_key: t\ndefault_persona: host\n",
        encoding="utf-8",
    )
    write_topic_index(tmp_path, "clean", title="C", creator="host")
    import cli.commands.fs as fs_cli

    monkeypatch.setattr(fs_cli, "_workspace", lambda: tmp_path)
    result = runner.invoke(app, ["topic", "anomalies"])
    assert result.exit_code == 0, result.output
    assert "(no anomalies)" in result.output


def test_fs_show_includes_anomaly_section_and_does_not_block(ws: Path) -> None:
    result = runner.invoke(app, ["topic", "show", "--topic", "t"])
    assert result.exit_code == 0, result.output
    # anomaly 段存在且读取照常（评论表仍在）
    assert "anomalies: 1" in result.output
    assert "round1-host.md" in result.output  # 合规评论正常列出
    assert "round1-participant.md" in result.output  # 脏文件本身也不阻断展示


def test_fs_comment_rejects_body_frontmatter_with_example(ws: Path) -> None:
    src = ws / "op.md"
    src.write_text(
        "---\nauthor: host\nround: 1\nposted_at: '$ts'\n---\n\n正文",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["topic", "comment", "--topic", "t", "--file", str(src)])
    assert result.exit_code == 2
    assert "must not carry its own frontmatter" in result.output
    # 拒绝文案带正确示例（D6）
    assert "正确形态" in result.output


def test_fs_comment_force_does_not_bypass_frontmatter_check(ws: Path) -> None:
    src = ws / "op2.md"
    src.write_text("---\nauthor: host\n---\n正文", encoding="utf-8")
    result = runner.invoke(
        app, ["topic", "comment", "--topic", "t", "--file", str(src), "--force"]
    )
    # --force 豁免 immutable，不豁免 W1
    assert result.exit_code == 2
    assert "must not carry its own frontmatter" in result.output
