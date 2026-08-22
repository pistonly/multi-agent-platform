"""v0.14 M60/M61：``map fs archive`` 薄命令 + ``archive-index`` 生成式投影（map_fs.archive）。

覆盖话题 v014-fs-archive-design round2 定稿验收：
- R-b 首跑 rebuild：新旧形态条目齐全不重复、legacy ``Status: open`` 失时（F2）修复
- R-c-② archive → undo 循环后 INDEX 与目录一致（经 rebuild 验证）；rebuild 幂等
- M60 前置校验：未 closed 拦截 / 目标存在拒绝覆盖 / 不存在目录报错
- M60 硬约束：archive/undo 不承载索引维护逻辑（索引一律经 rebuild 达成）
- 弱校验：``map/experiments/`` 文件级引用 → 警告信号（不阻断）
"""

from __future__ import annotations

from pathlib import Path

import pytest
from map_fs import update_topic_index, write_topic_index
from map_fs.archive import (
    ArchiveStateError,
    archive_topic,
    find_experiment_references,
    rebuild_archive_index,
    scan_archive_entries,
    unarchive_topic,
)


def _make_closed_topic(workspace: Path, slug: str, *, note: str | None = None) -> None:
    write_topic_index(workspace, slug, title=f"Topic {slug}", creator="host")
    update_topic_index(workspace, slug, status="closed", close_reason="done", close_note=note)


def _make_legacy_export_file(archive_topics: Path, name: str, *, status: str = "open") -> Path:
    """旧 export 形态：标题命名单文件 + 头部元数据 bullet（F2 失时的来源）。"""
    path = archive_topics / f"{name}.md"
    path.write_text(
        f"# {name}\n\n"
        f"- **Topic ID**: `00000000-0000-0000-0000-000000000001`\n"
        f"- **Status**: {status}\n"
        f"- **Discussion Round**: round1\n"
        f"- **Creator**: multi-agent-platform-host\n\n"
        "## Description\n\nlegacy export snapshot\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# M61 扫描与重建（生成式投影）
# ---------------------------------------------------------------------------


def test_scan_dual_form_and_f2_fix(tmp_path: Path) -> None:
    """R-b：双形态条目齐全；legacy 头部 Status: open 失时不采信（位置即状态 → closed）。"""
    archive_topics = tmp_path / "map" / "archive" / "topics"
    archive_topics.mkdir(parents=True)
    _make_legacy_export_file(archive_topics, "map-legacy-topic", status="open")
    _make_closed_topic(tmp_path, "new-form-topic", note='decision: "采用方案A"\nrationale: 收敛')
    archive_topic(tmp_path, "new-form-topic")  # 非 git tmp workspace → os.rename 路径

    entries = scan_archive_entries(tmp_path)
    by_rel = {e.file_rel: e for e in entries}
    assert set(by_rel) == {
        "topics/map-legacy-topic.md",
        "topics/new-form-topic/",
    }
    legacy = by_rel["topics/map-legacy-topic.md"]
    assert legacy.legacy is True
    assert legacy.status == "closed"  # F2 修复：不读 legacy 头部的 open
    assert legacy.decision == "no"
    new = by_rel["topics/new-form-topic/"]
    assert new.status == "closed"
    assert new.decision == "采用方案A"  # close_note 的 decision 字段
    assert new.notes == "done"


def test_rebuild_first_run_idempotent_and_table_intact(tmp_path: Path) -> None:
    """首跑重建 INDEX（表结构完好、无重复行）；重复 rebuild 幂等（R-c-②）。"""
    archive_topics = tmp_path / "map" / "archive" / "topics"
    archive_topics.mkdir(parents=True)
    _make_legacy_export_file(archive_topics, "legacy-a")
    _make_legacy_export_file(archive_topics, "legacy-b", status="closed")
    _make_closed_topic(tmp_path, "dir-form", note="decision: yes")
    archive_topic(tmp_path, "dir-form")

    index = rebuild_archive_index(tmp_path)
    text1 = index.read_text(encoding="utf-8")
    assert "## Topics (3)" in text1
    assert "| Title | Status | Decision | Notes | File |" in text1
    # 无重复行：每行条目唯一（按 file_rel 计数）
    rows = [ln for ln in text1.splitlines() if ln.startswith("| ") and "topics/" in ln]
    assert len(rows) == len(set(rows)) == 3

    text2 = rebuild_archive_index(tmp_path).read_text(encoding="utf-8")
    assert text2 == text1  # 幂等


def test_rebuild_on_empty_or_missing_archive(tmp_path: Path) -> None:
    """归档目录不存在时 rebuild 产出空表（不报错——生成式投影无状态可维护）。"""
    index = rebuild_archive_index(tmp_path)
    assert "## Topics (0)" in index.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# M60 archive / undo（薄命令行为）
# ---------------------------------------------------------------------------


def test_archive_requires_closed(tmp_path: Path) -> None:
    """未 closed 话题归档被拦截（exit 语义：ArchiveStateError → CLI exit 2）。"""
    write_topic_index(tmp_path, "still-open", title="Still Open", creator="host")
    with pytest.raises(ArchiveStateError, match="not closed"):
        archive_topic(tmp_path, "still-open")
    assert (tmp_path / "map" / "topics" / "still-open").is_dir()  # 未移动


def test_archive_missing_dir_and_target_exists(tmp_path: Path) -> None:
    with pytest.raises(ArchiveStateError, match="not found"):
        archive_topic(tmp_path, "ghost")
    _make_closed_topic(tmp_path, "dup-target")
    archive_topic(tmp_path, "dup-target")
    _make_closed_topic(tmp_path, "dup-target")  # 重新建同名（模拟还原后二次归档冲突）
    with pytest.raises(ArchiveStateError, match="already exists"):
        archive_topic(tmp_path, "dup-target")


def test_archive_undo_cycle_index_consistency(tmp_path: Path) -> None:
    """R-c-②：archive → undo 循环后 INDEX 与目录一致（经 rebuild 重建验证）。"""
    archive_topics = tmp_path / "map" / "archive" / "topics"
    archive_topics.mkdir(parents=True)
    _make_legacy_export_file(archive_topics, "legacy-keep")
    _make_closed_topic(tmp_path, "cycle-topic", note='decision: "close it"')
    archive_topic(tmp_path, "cycle-topic")
    rebuild_archive_index(tmp_path)

    assert (archive_topics / "cycle-topic").is_dir()
    assert not (tmp_path / "map" / "topics" / "cycle-topic").exists()
    text = (tmp_path / "map" / "archive" / "INDEX.md").read_text(encoding="utf-8")
    assert "topics/cycle-topic/" in text

    unarchive_topic(tmp_path, "cycle-topic")
    rebuild_archive_index(tmp_path)  # 索引一致性唯一路径：rebuild，undo 不碰 INDEX
    text = (tmp_path / "map" / "archive" / "INDEX.md").read_text(encoding="utf-8")
    assert "cycle-topic" not in text  # 还原后条目消失
    assert "legacy-keep" in text  # 其他条目不受影响
    assert (tmp_path / "map" / "topics" / "cycle-topic").is_dir()


def test_unarchive_validations(tmp_path: Path) -> None:
    with pytest.raises(ArchiveStateError, match="not found"):
        unarchive_topic(tmp_path, "ghost")
    _make_closed_topic(tmp_path, "u1")
    archive_topic(tmp_path, "u1")
    write_topic_index(tmp_path, "u1", title="Recreated", creator="host")  # 目标已存在
    with pytest.raises(ArchiveStateError, match="already exists"):
        unarchive_topic(tmp_path, "u1")


# ---------------------------------------------------------------------------
# 弱校验（零 API 边界：文件级引用信号，警告不阻断）
# ---------------------------------------------------------------------------


def test_find_experiment_references(tmp_path: Path) -> None:
    experiments = tmp_path / "map" / "experiments" / "exp-a"
    experiments.mkdir(parents=True)
    (experiments / "plan.md").write_text(
        "---\ntitle: E\n---\n依赖话题 v014-fs-archive-design 的定稿结论\n", encoding="utf-8"
    )
    (tmp_path / "map" / "experiments" / "other.md").write_text("无关内容\n", encoding="utf-8")

    refs = find_experiment_references(tmp_path, "v014-fs-archive-design")
    assert refs == ["map/experiments/exp-a/plan.md"]
    assert find_experiment_references(tmp_path, "no-such-slug") == []


def test_archive_proceeds_with_references(tmp_path: Path) -> None:
    """弱校验只产生警告信号（调用方输出），不阻断 archive 本身。"""
    _make_closed_topic(tmp_path, "referenced-topic")
    (tmp_path / "map" / "experiments").mkdir(parents=True)
    (tmp_path / "map" / "experiments" / "plan.md").write_text(
        "引用 referenced-topic\n", encoding="utf-8"
    )
    assert find_experiment_references(tmp_path, "referenced-topic") != []
    archive_topic(tmp_path, "referenced-topic")  # 不阻断


def test_archived_topics_leave_scan_plane(tmp_path: Path) -> None:
    """归档话题退出扫描面（map work / fs list 同源回归）：scan_plane 不含归档目录。

    位置即状态——扫描面排除依赖「plane 只扫 map/topics/」这一物理约定，
    本测试防回退（防止未来 scan_plane 误扩到 archive 目录）。
    """
    from map_fs import scan_plane

    _make_closed_topic(tmp_path, "scanned-then-archived")
    write_topic_index(tmp_path, "still-active", title="Active", creator="host")
    archive_topic(tmp_path, "scanned-then-archived")

    plane = scan_plane(tmp_path)
    assert [t.slug for t in plane.topics] == ["still-active"]
