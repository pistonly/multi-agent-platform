"""fs-write-entry-validation（27f961d1）双端校验测试。

- W1 写路径前置拒收：body 自带 frontmatter（author/round/posted_at 任一机字段）→ 拒绝；
  --force/overwrite 不豁免；纯 Markdown 分隔线/无机器字段 YAML 放行。
- R1 读路径 anomaly：复用 _ack_error_of 三条判定，invalid/lite 分级。
- R2 不阻断不改写：读取行为不变（posted_at→mtime fallback、正文完整），anomaly 只报告。
- E1 真实脏 fixture（`fs-close-action-items-lifecycle/round1-host.md` posted_at='$ts'；closed 后在 map/archive/topics/）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from map_fs import (
    parse_topic_dir,
    scan_plane,
    write_round_comment,
    write_topic_index,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# W1 写路径前置校验
# ---------------------------------------------------------------------------


def _write_index(ws: Path, slug: str = "v-demo") -> None:
    write_topic_index(ws, slug, title="V Demo", creator="host", description="")


@pytest.mark.parametrize(
    "body",
    [
        "---\nauthor: host\nround: 1\nposted_at: '2026-01-01T00:00:00+00:00'\n---\n# x",
        "---\nauthor: host\nround: 1\n---\n# x",
        "---\nauthor: host\n---\n# x",
        "---\nround: 1\n---\n# x",
        "---\nposted_at: '2026-01-01'\n---\n# x",
    ],
)
def test_write_rejects_embedded_frontmatter(tmp_path: Path, body: str) -> None:
    _write_index(tmp_path)
    with pytest.raises(ValueError, match="must not carry its own frontmatter"):
        write_round_comment(tmp_path, "v-demo", round_number=1, persona="host", body=body)


def test_write_rejects_embedded_frontmatter_even_with_overwrite(tmp_path: Path) -> None:
    _write_index(tmp_path)
    with pytest.raises(ValueError, match="must not carry its own frontmatter"):
        write_round_comment(
            tmp_path,
            "v-demo",
            round_number=1,
            persona="host",
            body="---\nauthor: host\n---\n# x",
            overwrite=True,
        )


def test_write_allows_plain_body(tmp_path: Path) -> None:
    _write_index(tmp_path)
    write_round_comment(tmp_path, "v-demo", round_number=1, persona="host", body="# 我的发言")
    topic = scan_plane(tmp_path).topics[0]
    assert topic.comments[0].content.strip() == "# 我的发言"
    assert topic.comments[0].posted_at is not None  # CLI 生成，非常用脏值


def test_write_allows_markdown_separator(tmp_path: Path) -> None:
    """`---` 起手但无闭合围栏（Markdown 分隔线）→ 不是 frontmatter，放行。"""
    _write_index(tmp_path)
    write_round_comment(
        tmp_path,
        "v-demo",
        round_number=1,
        persona="host",
        body="---\n上面的横线是分隔线\n正文照常",
    )
    c = scan_plane(tmp_path).topics[0].comments[0]
    assert c.content.strip().startswith("---")  # 分隔线随正文保留
    assert c.round == 1 and c.author == "host"


def test_write_allows_yaml_without_machine_keys(tmp_path: Path) -> None:
    """含 YAML 块但无 author/round/posted_at 任一机字段 → 不拒（非评论 frontmatter）。"""
    _write_index(tmp_path)
    write_round_comment(
        tmp_path, "v-demo", round_number=1, persona="host", body="---\ntitle: 我的笔记\n---\n正文"
    )
    c = scan_plane(tmp_path).topics[0].comments[0]
    assert "正文" in c.content  # title 作为无关 YAML 未被拦截，正文保留
    assert c.round == 1 and c.author == "host"


# ---------------------------------------------------------------------------
# R1 读路径 anomaly 收集 + R2 不阻断不改写
# ---------------------------------------------------------------------------


def _dirty_round_file(ws: Path, slug: str, fname: str, fm: str, body: str = "# 正文") -> None:
    d = ws / "map" / "topics" / slug  # 与 write_topic_index 同 content_root="map"
    d.mkdir(parents=True, exist_ok=True)
    (d / fname).write_text(f"{fm}\n\n{body}\n", encoding="utf-8")


def test_anomaly_invalid_author_mismatch(tmp_path: Path) -> None:
    _write_index(tmp_path, "a")
    _dirty_round_file(
        tmp_path, "a", "round1-host.md", "---\nauthor: participant\nround: 1\n---"
    )
    topic = scan_plane(tmp_path).topics[0]
    assert topic.anomalies
    an = topic.anomalies[0]
    assert an.file == "map/topics/a/round1-host.md"  # 相对 workspace 含 content_root
    assert an.level == "invalid"
    assert "author" in an.reason
    # R2：正文完整、posted_at fallback mtime，读取不阻断
    assert topic.comments[0].content.strip() == "# 正文"
    assert topic.comments[0].posted_at is not None


def test_anomaly_invalid_posted_at_unparseable(tmp_path: Path) -> None:
    _write_index(tmp_path, "a")
    _dirty_round_file(
        tmp_path,
        "a",
        "round1-host.md",
        "---\nauthor: host\nround: 1\nposted_at: '$ts'\n---",
    )
    topic = scan_plane(tmp_path).topics[0]
    an = topic.anomalies[0]
    assert an.level == "invalid"
    assert "unparseable" in an.reason


def test_anomaly_lite_posted_at_missing(tmp_path: Path) -> None:
    _write_index(tmp_path, "a")
    _dirty_round_file(tmp_path, "a", "round1-host.md", "---\nauthor: host\nround: 1\n---")
    topic = scan_plane(tmp_path).topics[0]
    an = topic.anomalies[0]
    assert an.level == "lite"
    assert "posted_at" in an.reason
    assert topic.comments[0].posted_at is not None  # fallback mtime，不阻断


def test_anomaly_lite_frontmatter_totally_missing(tmp_path: Path) -> None:
    _write_index(tmp_path, "a")
    _dirty_round_file(tmp_path, "a", "round1-host.md", "# 无 frontmatter 的手写文件")
    topic = scan_plane(tmp_path).topics[0]
    assert any(a.level == "lite" for a in topic.anomalies)


def test_clean_round_file_has_no_anomaly(tmp_path: Path) -> None:
    _write_index(tmp_path, "a")
    write_round_comment(tmp_path, "a", round_number=1, persona="host", body="# 干净的发言")
    topic = scan_plane(tmp_path).topics[0]
    assert topic.anomalies == []
    assert topic.comments[0].posted_at is not None


# ---------------------------------------------------------------------------
# E1 真实脏 fixture 锚点（posted_at='$ts'）
# ---------------------------------------------------------------------------


def test_e1_anchor_real_dirty_fixture_reported_and_non_blocking() -> None:
    live = REPO_ROOT / "map" / "topics" / "fs-close-action-items-lifecycle"
    archived = (
        REPO_ROOT / "map" / "archive" / "topics" / "fs-close-action-items-lifecycle"
    )
    fixture = live if (live / "round1-host.md").is_file() else archived
    if not (fixture / "round1-host.md").is_file():
        pytest.skip("真实脏 fixture 未检出（本地仓库专用锚点）")
    topic = parse_topic_dir(fixture, REPO_ROOT)
    assert topic is not None
    an = next((a for a in topic.anomalies if a.file.endswith("round1-host.md")), None)
    assert an is not None, "posted_at='$ts' 脏文件必须被报告为 invalid anomaly"
    assert an.level == "invalid"
    assert "unparseable" in an.reason
    c = next((c for c in topic.comments if c.author == "host" and c.round == 1), None)
    assert c is not None
    assert "发起帖" in c.content  # 读不阻断，正文完整
    assert c.posted_at is not None  # posted_at fallback 到 mtime
