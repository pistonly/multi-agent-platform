"""话题执行项载体 action-items.yaml 的解析与验证型写回（A1：错漏不静默）。"""

from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]

from map_fs.frontmatter import _parse_dt, _require_slug
from map_fs.index_io import _atomic_write
from map_fs.model import DEFAULT_CONTENT_ROOT, FsActionItem

# 话题执行项载体：action-items.yaml（列表文档，无 front-matter 围栏）
_ACTION_ITEMS_FILE = "action-items.yaml"
_ACTION_ITEM_STATUSES = {"open", "done", "cancelled"}


def parse_action_items_file(path: Path) -> tuple[list[FsActionItem], str | None]:
    """解析话题文件夹的 action-items.yaml → (items, error)。

    文件缺失 → ``([], None)``。列表文档每项字段：id/title/owner/status
    (open|done|cancelled)/evidence/reason/created_at。格式错漏（非列表、
    缺必填、status 非法、id 重复）返回 ``([], 错误文案)``——不静默（A1）：
    server close 门禁与 CLI 写回都据此拒绝，并在 409 / 报错里给出可读引导。
    """
    if not path.is_file():
        return [], None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        return [], f"{path.name} 不是合法 YAML: {err}"
    if raw is None:
        return [], None  # 空文件视作空列表（无执行项）
    if not isinstance(raw, list):
        return [], f"{path.name} 应为列表文档(list)，实际是 {type(raw).__name__}"
    items: list[FsActionItem] = []
    for idx, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            return [], f"{path.name} 第 {idx} 项不是对象(dict)"
        title = entry.get("title")
        owner = entry.get("owner")
        status = entry.get("status")
        if not isinstance(title, str) or not title.strip():
            return [], f"{path.name} 第 {idx} 项 title 缺失或为空"
        if not isinstance(owner, str) or not owner.strip():
            return [], f"{path.name} 第 {idx} 项 owner 缺失或为空"
        if status not in _ACTION_ITEM_STATUSES:
            return [], f"{path.name} 第 {idx} 项 status={status!r} 非法(可选 open|done|cancelled)"
        raw_id = entry.get("id")
        # id 可省略：按文档顺序 1-based 兜底（手写友好）；显式 id 须正整数
        if raw_id is None:
            raw_id = idx
        if not isinstance(raw_id, int) or raw_id <= 0:
            return [], f"{path.name} 第 {idx} 项 id={raw_id!r} 非正整数"
        items.append(
            FsActionItem(
                id=raw_id,
                title=str(title).strip(),
                owner=str(owner).strip(),
                status=status,
                evidence=str(entry.get("evidence") or "").strip(),
                reason=str(entry.get("reason") or "").strip(),
                created_at=_parse_dt(entry.get("created_at")),
            )
        )
    ids = [it.id for it in items]
    if len(ids) != len(set(ids)):
        return [], f"{path.name} 存在重复 id: {sorted(set(x for x in ids if ids.count(x) > 1))}"
    return items, None


def read_action_items(
    workspace: Path,
    slug: str,
    *,
    content_root: str = DEFAULT_CONTENT_ROOT,
) -> tuple[list[FsActionItem], str | None]:
    """读话题文件夹的 action-items.yaml；缺失 → ([], None)。"""
    slug = _require_slug(slug)
    topic_dir = workspace / content_root / "topics" / slug
    return parse_action_items_file(topic_dir / _ACTION_ITEMS_FILE)


def write_action_items(
    workspace: Path,
    slug: str,
    items: list[FsActionItem],
    *,
    content_root: str = DEFAULT_CONTENT_ROOT,
) -> Path:
    """原子写回话题文件夹的 action-items.yaml（全量覆盖，列表文档无围栏）。

    每次写回规范化落盘（显式 id / created_at），空列表也写空文件——文件
    存在即接管该话题执行项（close 门禁对"无文件"话题不设限）。
    """
    slug = _require_slug(slug)
    payload = [
        {
            "id": item.id,
            "title": item.title,
            "owner": item.owner,
            "status": item.status,
            "evidence": item.evidence or None,
            "reason": item.reason or None,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in items
    ]
    text = (
        yaml.safe_dump(
            payload,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).strip()
        + "\n"
    )
    topic_dir = workspace / content_root / "topics" / slug
    path = topic_dir / _ACTION_ITEMS_FILE
    _atomic_write(path, text)
    return path
