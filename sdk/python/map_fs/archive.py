"""``map/archive/`` 归档域：扫描、INDEX.md 生成式重建、归档/还原移动。

v0.14 M60/M61 定稿（话题 v014-fs-archive-design round2）：

* **位置即状态**（M58 延伸）：归档 = ``map/topics/<slug>/`` 移入
  ``map/archive/topics/``；在归档目录里的话题状态即 ``closed``——
  legacy export 单文件头部的陈旧 Status 值不采信（F2 失时由首跑 rebuild 修复）。
* **生成式投影**（M61）：INDEX.md 是 ``map/archive/topics/`` 的可派生投影，
  **生成而非维护**——不做增量 helper、不做并发原子写、不承担时效责任，
  时效由「谁归档谁来扫」（archive 成功后自动 rebuild）保证。
* **薄命令**（M60）：archive/undo 只做校验 + 移动，不承载索引维护逻辑；
  索引一致性一律经 :func:`rebuild_archive_index` 达成。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from map_fs.parser import (
    DEFAULT_CONTENT_ROOT,
    _atomic_write,
    parse_front_matter,
    parse_topic_dir,
)

_DECISION_RE = re.compile(r'^decision:\s*["\']?(.+?)["\']?\s*$', re.M)
_LEGACY_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$", re.M)
_LEGACY_STATUS_RE = re.compile(r"^-\s+\*\*Status\*\*:\s*(\S+)", re.M)

INDEX_HEADER = """# Archive Index

> Generated projection of `{root}/archive/topics/` — do not edit by hand.
> Rebuilt by `map fs archive-index --rebuild` (auto-invoked after `map fs archive`).
> v0.14 M61: generative projection, not an incrementally-maintained document.
"""


class ArchiveStateError(Exception):
    """归档前置校验不满足（exit 2 场景），message 面向用户可读。"""


@dataclass
class ArchiveEntry:
    """INDEX.md 的一行；目录形态与 legacy export 单文件形态统一到这里。"""

    title: str
    status: str  # 位置即状态：归档目录内一律 closed
    decision: str  # close_note 的 decision 字段，缺失记 "no"
    notes: str  # 目录形态取 close_reason；legacy 标注形态来源
    file_rel: str  # 相对 map/archive/ 的 posix 路径
    legacy: bool = False


def _topics_dir(workspace: Path, content_root: str) -> Path:
    return workspace / content_root / "topics"


def _archive_dir(workspace: Path, content_root: str) -> Path:
    return workspace / content_root / "archive" / "topics"


def _decision_from_note(note: object) -> str:
    if not note:
        return "no"
    match = _DECISION_RE.search(str(note))
    return match.group(1).strip() if match else "no"


def _entry_from_dir(topic_dir: Path, workspace: Path) -> ArchiveEntry | None:
    """目录形态 ``<slug>/``：复用 parse_topic_dir，close_note/decision 取自 index frontmatter。"""
    topic = parse_topic_dir(topic_dir, workspace)
    if topic is None:
        return None
    meta, _ = parse_front_matter((topic_dir / "index.md").read_text(encoding="utf-8"))
    return ArchiveEntry(
        title=topic.title,
        status="closed",  # 位置即状态；归档前置校验已保证 closed，不读旧值
        decision=_decision_from_note(meta.get("close_note")),
        notes=str(meta.get("close_reason") or ""),
        file_rel=f"topics/{topic.slug}/",
    )


def _entry_from_legacy_file(path: Path) -> ArchiveEntry:
    """legacy export 单文件形态（标题命名 .md）：读文件头的 Title/Status bullet。

    Status 仅作 Notes 参考——归档目录内状态一律 closed（F2 失时修复依据）。
    """
    text = path.read_text(encoding="utf-8")
    title_match = _LEGACY_TITLE_RE.search(text)
    status_match = _LEGACY_STATUS_RE.search(text)
    title = title_match.group(1).strip() if title_match else path.stem
    stale = status_match.group(1) if status_match else "?"
    return ArchiveEntry(
        title=title,
        status="closed",
        decision="no",
        notes=f"legacy export form (header said: {stale})",
        file_rel=f"topics/{path.name}",
        legacy=True,
    )


def scan_archive_entries(
    workspace: Path, content_root: str = DEFAULT_CONTENT_ROOT
) -> list[ArchiveEntry]:
    """扫描 ``map/archive/topics/`` 全量条目（双形态解析，输出按路径稳定排序）。

    M61 单一扫描函数：rebuild 命令与 archive 成功后的自动重建共用本实现。
    """
    archive_dir = _archive_dir(workspace, content_root)
    if not archive_dir.is_dir():
        return []
    entries: list[ArchiveEntry] = []
    for entry in sorted(archive_dir.iterdir()):
        if entry.is_dir():
            parsed = _entry_from_dir(entry, workspace)
            if parsed is not None:
                entries.append(parsed)
        elif entry.is_file() and entry.suffix == ".md":
            entries.append(_entry_from_legacy_file(entry))
    entries.sort(key=lambda e: e.file_rel)
    return entries


def render_archive_index(
    entries: list[ArchiveEntry], content_root: str = DEFAULT_CONTENT_ROOT
) -> str:
    """渲染 INDEX.md 全文（生成式投影，无增量状态）。"""
    lines = [
        INDEX_HEADER.format(root=content_root).rstrip("\n"),
        "",
        f"## Topics ({len(entries)})",
        "",
        "| Title | Status | Decision | Notes | File |",
        "|-------|--------|----------|-------|------|",
    ]
    for e in entries:
        lines.append(
            f"| {e.title} | {e.status} | {e.decision} | {e.notes} | [{e.file_rel}]({e.file_rel}) |"
        )
    return "\n".join(lines) + "\n"


def rebuild_archive_index(
    workspace: Path, content_root: str = DEFAULT_CONTENT_ROOT
) -> Path:
    """全量重建 ``map/archive/INDEX.md``（幂等；tmp+replace 落盘为卫生习惯）。"""
    entries = scan_archive_entries(workspace, content_root)
    index_path = workspace / content_root / "archive" / "INDEX.md"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(index_path, render_archive_index(entries, content_root))
    return index_path


# ---------------------------------------------------------------------------
# M60 归档 / 还原（薄命令：校验 + 移动，无索引维护逻辑）
# ---------------------------------------------------------------------------


def _is_git_workspace(workspace: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _move(src: Path, dst: Path, workspace: Path) -> None:
    """git workspace 下 ``git mv``（前置执行，stage 后 status 显示 renamed:）；非 git ``os.rename``。

    git mv 失败直接抛错不静默 fallback——os.rename 会退化成 deleted+untracked，
    破坏 rename 保真验收；半移动状态由用户按报错信息处置。
    """
    if _is_git_workspace(workspace):
        result = subprocess.run(
            ["git", "mv", str(src), str(dst)],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise ArchiveStateError(
                f"git mv failed ({result.stderr.strip() or result.stdout.strip()}); "
                "nothing was moved — resolve manually (e.g. uncommitted conflicts)"
            )
    else:
        import os

        os.rename(src, dst)


def find_experiment_references(
    workspace: Path, slug: str, content_root: str = DEFAULT_CONTENT_ROOT
) -> list[str]:
    """弱校验：grep ``map/experiments/**/*.md`` 中对该 slug 的引用（零 API 边界）。

    实验关联的完备判定在 DB（phase 不可见），此处仅文件级信号：命中输出
    警告（列出引用文件）但不阻断——「无活跃实验关联」弱校验 + 警告的定稿口径。
    """
    refs: list[str] = []
    experiments_dir = workspace / content_root / "experiments"
    if not experiments_dir.is_dir():
        return refs
    for path in sorted(experiments_dir.rglob("*.md")):
        try:
            if slug in path.read_text(encoding="utf-8"):
                refs.append(path.relative_to(workspace).as_posix())
        except OSError:
            continue
    return refs


def archive_topic(
    workspace: Path,
    slug: str,
    *,
    content_root: str = DEFAULT_CONTENT_ROOT,
) -> Path:
    """``map fs archive``：校验 → 移动 → 自动 rebuild（索引一致性唯一路径）。

    前置校验（任一不满足抛 :class:`ArchiveStateError`，不静默成功）：
    目录存在且含 index.md / status: closed / 目标不存在。
    """
    src = _topics_dir(workspace, content_root) / slug
    index_path = src / "index.md"
    if not src.is_dir() or not index_path.is_file():
        raise ArchiveStateError(f"topic dir not found or missing index.md: {src}")
    meta, _ = parse_front_matter(index_path.read_text(encoding="utf-8"))
    status = str(meta.get("status") or "open").lower()
    if status != "closed":
        raise ArchiveStateError(
            f"topic '{slug}' is {status} (not closed) — run `map fs close --topic {slug}` first"
        )
    dst = _archive_dir(workspace, content_root) / slug
    if dst.exists():
        raise ArchiveStateError(f"archive target already exists: {dst} (refusing to overwrite)")
    dst.parent.mkdir(parents=True, exist_ok=True)
    _move(src, dst, workspace)
    return dst


def unarchive_topic(
    workspace: Path,
    slug: str,
    *,
    content_root: str = DEFAULT_CONTENT_ROOT,
) -> Path:
    """``map fs archive --undo``：反向三步薄层（校验 archive 侧存在 & 目标不存在 → 移回）。

    硬约束：不承载任何索引维护逻辑；还原后索引一致性由自动 rebuild 达成。
    """
    src = _archive_dir(workspace, content_root) / slug
    if not src.is_dir():
        raise ArchiveStateError(f"archived topic not found: {src}")
    dst = _topics_dir(workspace, content_root) / slug
    if dst.exists():
        raise ArchiveStateError(f"restore target already exists: {dst} (refusing to overwrite)")
    _move(src, dst, workspace)
    return dst
