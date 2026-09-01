"""front-matter / excerpt 基础设施（读侧与写侧共用的容错原语）。"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

_EXCERPT_MAX = 200
_FM_FENCE = "---"


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """解析 ``---`` 围栏的 YAML front-matter，返回 (meta, body)。

    无围栏或 YAML 解析失败时返回 ({}, 原文)，永不抛错——
    事实源是手写文件，容错优先。
    """
    if not text.startswith(_FM_FENCE):
        return {}, text
    lines = text.splitlines(keepends=True)
    if len(lines) < 2:
        return {}, text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == _FM_FENCE:
            fm_text = "".join(lines[1:idx])
            body = "".join(lines[idx + 1 :])
            try:
                meta = yaml.safe_load(fm_text)
            except yaml.YAMLError:
                return {}, text
            return meta if isinstance(meta, dict) else {}, body
    return {}, text


def make_excerpt(body: str, limit: int = _EXCERPT_MAX) -> str:
    """首个一级标题（或首个非空行）作为摘要，截断到 limit。"""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# "):
            stripped = stripped[2:].strip()
        return stripped[:limit]
    return ""


def _parse_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _mtime_utc(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _coerce_participants(raw: object) -> list[str]:
    """front-matter ``participants`` 容错归一：list / 单字符串 → 去空去重列表。

    手写 YAML 常见 ``participants: host`` 单值写法按单元素处理；其余类型
    （数字、dict 等）视为未声明，返回空列表。
    """
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    seen: list[str] = []
    for item in raw:
        name = str(item).strip() if item is not None else ""
        if name and name not in seen:
            seen.append(name)
    return seen


def slugify(text: str) -> str:
    """标题 → 文件夹安全的 slug（小写、连字符、剔除其余）。"""
    slug = re.sub(r"[^a-zA-Z0-9\-_\u4e00-\u9fff]+", "-", text.strip().lower())
    slug = slug.strip("-")
    return slug or f"topic-{uuid.uuid4().hex[:8]}"


def _require_slug(slug: str) -> str:
    """空 slug 防护：部分 typer/click 版本组合（实测 0.16.1 + 8.4.x）不强制
    校验必填 CLI 选项，None 会一路穿透到这里的路径拼接，抛出难懂的
    ``TypeError: PosixPath / NoneType``。SDK 层提前给出可读错误。"""
    if not slug or not str(slug).strip():
        raise ValueError(f"slug must be a non-empty folder name, got: {slug!r}")
    return slug
