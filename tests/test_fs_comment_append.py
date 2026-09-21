"""map_fs 层 append_round_comment：发言后补充内容的合法通道（只增不改）。

配套 ``write_round_comment`` 的 immutable 约定：原正文不可篡改，补充内容
以 ``## Addendum <n>`` 小节追加，front-matter 记 ``updated_at``。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from map_fs import append_round_comment, scan_plane, write_round_comment, write_topic_index
from map_fs.frontmatter import parse_front_matter


def _setup(tmp_path: Path) -> Path:
    write_topic_index(tmp_path, "ap", title="Append", creator="host")
    return write_round_comment(
        tmp_path, "ap", round_number=1, persona="host", body="# 原始发言\n正文A"
    )


def test_append_adds_addendum_and_keeps_original(tmp_path: Path) -> None:
    path = _setup(tmp_path)
    original = path.read_text(encoding="utf-8")
    meta_before, _ = parse_front_matter(original)

    out = append_round_comment(
        tmp_path, "ap", round_number=1, persona="host", body="补充：数据口径修正"
    )
    assert out == path

    text = path.read_text(encoding="utf-8")
    # 原正文完整保留，补充内容以 Addendum 小节追加
    assert "# 原始发言" in text and "正文A" in text
    assert "补充：数据口径修正" in text
    assert re.search(r"^## Addendum 1 @ .+$", text, re.MULTILINE)
    # 原正文在 Addendum 小节之前（只增不改）
    assert text.index("正文A") < text.index("## Addendum 1")

    meta_after, _ = parse_front_matter(text)
    # posted_at 原值保留；updated_at 记录追加时间
    assert str(meta_after.get("posted_at")) == str(meta_before.get("posted_at"))
    assert meta_after.get("updated_at") is not None
    # 解析层照常：author/round 不变，正文含两者
    comments = scan_plane(tmp_path).topics[0].comments
    assert len(comments) == 1
    assert comments[0].author == "host"
    assert "正文A" in comments[0].content and "补充：数据口径修正" in comments[0].content


def test_append_numbering_increments(tmp_path: Path) -> None:
    _setup(tmp_path)
    append_round_comment(tmp_path, "ap", round_number=1, persona="host", body="第一次补充")
    append_round_comment(tmp_path, "ap", round_number=1, persona="host", body="第二次补充")
    text = (tmp_path / "map" / "topics" / "ap" / "round1-host.md").read_text(
        encoding="utf-8"
    )
    assert "## Addendum 1" in text and "## Addendum 2" in text
    assert text.index("## Addendum 1") < text.index("## Addendum 2")


def test_append_missing_file_raises(tmp_path: Path) -> None:
    write_topic_index(tmp_path, "empty", title="E", creator="host")
    with pytest.raises(FileNotFoundError, match="nothing to append"):
        append_round_comment(tmp_path, "empty", round_number=1, persona="host", body="x")


def test_append_rejects_embedded_frontmatter(tmp_path: Path) -> None:
    _setup(tmp_path)
    with pytest.raises(ValueError, match="must not carry its own frontmatter"):
        append_round_comment(
            tmp_path,
            "ap",
            round_number=1,
            persona="host",
            body="---\nauthor: host\n---\n伪装正文",
        )


def test_append_rejects_frontmatter_mismatch(tmp_path: Path) -> None:
    path = _setup(tmp_path)
    # 模拟手改：文件名是 host，frontmatter author 却是 participant
    path.write_text(
        re.sub(r"author: host", "author: participant", path.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not match append args"):
        append_round_comment(tmp_path, "ap", round_number=1, persona="host", body="补充")


def test_append_rejects_round_mismatch(tmp_path: Path) -> None:
    path = _setup(tmp_path)
    # 模拟手改：文件名是 round1，frontmatter round 却写成 3
    path.write_text(
        re.sub(r"^round: 1$", "round: 3", path.read_text(encoding="utf-8"), flags=re.MULTILINE),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not match append args"):
        append_round_comment(tmp_path, "ap", round_number=1, persona="host", body="补充")
