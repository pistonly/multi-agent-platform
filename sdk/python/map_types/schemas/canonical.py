"""规范化 hash 模块（M2 A1）— Markdown / YAML 规范化 + 五路 SHA-256。

规范化规则（与实验 1b7935e0 plan D2 / acceptance A1 一致）：

- **Markdown**（index.md / plan.md / log.md）：UTF-8 NFC + LF（CRLF / CR → LF）+
  末尾恰好一个换行（无尾换行补一个；多个收成一个）
- **YAML**（reviews/*.yaml）：yaml.safe_load 后 **递归排序 mapping key**、
  **保持 list 顺序**、dump 成稳定 JSON 编码
- **content_root**：走 ``normalize_content_root``（单目录名、字母数字下划线短横线点）

五路 hash（hex SHA-256，64 字符）：

1. ``metadata_hash``：FsExperimentRead 规范化为 dict 后 JSON dump + sort_keys + SHA-256
2. ``plan_content_hash``：plan.md 文本经 ``normalize_markdown`` 后 SHA-256
3. ``log_content_hash``：log.md 文本经 ``normalize_markdown`` 后 SHA-256
4. ``reviews_content_hash``：reviews/*.yaml 文件按文件名排序，逐个 ``normalize_yaml``
   后 JSON 拼接 + SHA-256
5. ``content_root_hash``：content_root 字符串 SHA-256（亦作跨 project 锚）

公开 API：

- 文本规范化：``normalize_markdown`` / ``normalize_yaml_payload`` / ``normalize_yaml_text``
- 五路 hash：``metadata_hash`` / ``plan_content_hash`` / ``log_content_hash`` /
  ``reviews_content_hash`` / ``content_root_hash``
- 校验：``validate_markdown_normalization`` / ``validate_yaml_normalization`` — 断言
  规范化是幂等的（normalize ∘ normalize = normalize）

历史兼容：现有 ``fs_experiment_content_hash``（fs.py）保留；本模块是 A1 增量，
**不替代** projection cache 现状。仅在 ``metadata_hash`` 调用处与
``fs_experiment_content_hash`` 输出一致（同一 dict → 同一 hash）。
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterable
from typing import Any

import yaml  # type: ignore[import-untyped]

from map_types.schemas.fs import FsExperimentRead
from map_types.schemas.project import normalize_content_root

# ---------------------------------------------------------------------------
# Markdown 规范化
# ---------------------------------------------------------------------------


def normalize_markdown(text: str) -> str:
    """规范化 Markdown / index.md 文本：

    1. NFC：``unicodedata.normalize("NFC", text)``
    2. 行尾：CRLF / CR → LF
    3. 末尾：恰好一个 LF（无补一个，多个收成一个）

    返回字符串（str）；调用方负责 .encode("utf-8")。
    """
    nfc = unicodedata.normalize("NFC", text)
    lf_only = nfc.replace("\r\n", "\n").replace("\r", "\n")
    if not lf_only.endswith("\n"):
        lf_only += "\n"
    else:
        # 收尾多个 \n → 一个
        lf_only = lf_only.rstrip("\n") + "\n"
    return lf_only


def validate_markdown_normalization(text: str) -> None:
    """断言 ``normalize_markdown`` 幂等：normalize ∘ normalize = normalize。"""
    once = normalize_markdown(text)
    twice = normalize_markdown(once)
    assert once == twice, f"markdown normalize not idempotent: {once!r} != {twice!r}"
    # 末尾必为一个 \n
    assert once.count("\n") >= 1 and once[-1] == "\n", "markdown must end with exactly one LF"


# ---------------------------------------------------------------------------
# YAML 规范化（解析 → 递归排序 mapping key → 稳定 JSON）
# ---------------------------------------------------------------------------


def _sort_mapping_recursive(value: Any) -> Any:
    """递归把 dict key 排序；保持 list 顺序；标量原样返回。"""
    if isinstance(value, dict):
        return {k: _sort_mapping_recursive(value[k]) for k in sorted(value.keys())}
    if isinstance(value, list):
        return [_sort_mapping_recursive(item) for item in value]
    return value


def normalize_yaml_payload(payload: Any) -> Any:
    """对已经解析的 YAML payload 应用「递归排序 mapping key」规则。

    list 顺序保持不变；标量原样返回。
    """
    return _sort_mapping_recursive(payload)


def normalize_yaml_text(text: str) -> Any:
    """解析 + 排序：``yaml.safe_load`` + ``normalize_yaml_payload``。

    返回 dict / list / 标量（取决于 YAML 内容）；调用方负责序列化。
    """
    loaded = yaml.safe_load(text)
    return _sort_mapping_recursive(loaded)


def _yaml_payload_to_jsonable(payload: Any) -> str:
    """稳定 JSON 编码：sort_keys=True、separators=(",", ":")、ensure_ascii=False。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_yaml_normalization(text: str) -> None:
    """断言 ``normalize_yaml_text`` 与「重新 dump + 再解析」产生相同 dict（幂等）。"""
    once = normalize_yaml_text(text)
    once_json = _yaml_payload_to_jsonable(once)
    twice = yaml.safe_load(once_json)
    twice_json = _yaml_payload_to_jsonable(twice)
    assert once_json == twice_json, (
        f"yaml normalize not idempotent: {once_json!r} != {twice_json!r}"
    )


# ---------------------------------------------------------------------------
# 五路 hash
# ---------------------------------------------------------------------------


def _sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_text(text: str) -> str:
    """规范化 + UTF-8 + SHA-256 hex。"""
    return _sha256_hex(text.encode("utf-8"))


def metadata_hash(experiment: FsExperimentRead) -> str:
    """FsExperimentRead 元数据规范化 hash。

    与 ``fs_experiment_content_hash`` 同源（同一 dict + 同一 JSON 编码），保留
    作为 A1 的对外稳定入口；projection cache 内部仍可继续用 fs.py 原函数。
    """
    payload = experiment.model_dump(mode="json", exclude={"created_at", "dir_path"})
    return _sha256_text(_yaml_payload_to_jsonable(payload))


def plan_content_hash(text: str) -> str:
    """plan.md 规范化 hash（Markdown 规则）。"""
    return _sha256_text(normalize_markdown(text))


def log_content_hash(text: str) -> str:
    """log.md 规范化 hash（Markdown 规则）。"""
    return _sha256_text(normalize_markdown(text))


def reviews_content_hash(reviews: Iterable[tuple[str, str]]) -> str:
    """reviews/*.yaml 规范化拼接 hash。

    ``reviews`` 是 ``(slug, text)`` 可迭代对象；按 slug 排序后逐个 normalize，
    拼接为 ``"<slug>\\n<normalized_json>\\n"`` 块再 SHA-256。slug 排序确保
    跨客户端顺序稳定。
    """
    parts: list[str] = []
    for slug in sorted(slug for slug, _ in reviews):
        for s, t in reviews:
            if s == slug:
                payload = normalize_yaml_text(t)
                parts.append(f"{slug}\n{_yaml_payload_to_jsonable(payload)}\n")
                break
    return _sha256_text("".join(parts))


def content_root_hash(content_root: str) -> str:
    """content_root 字符串 SHA-256（先走 ``normalize_content_root`` 校验格式）。"""
    normalized = normalize_content_root(content_root)
    return _sha256_text(normalized)


# ---------------------------------------------------------------------------
# 一次性 derive：五路 hash 一次性产出
# ---------------------------------------------------------------------------


def all_hashes(
    *,
    experiment: FsExperimentRead,
    plan_text: str | None = None,
    log_text: str | None = None,
    reviews: Iterable[tuple[str, str]] | None = None,
    content_root: str = "map",
) -> dict[str, str]:
    """一次性产出五路 hash；任一文本缺失则对应 hash 为空串。"""
    return {
        "metadata_hash": metadata_hash(experiment),
        "plan_content_hash": plan_content_hash(plan_text) if plan_text else "",
        "log_content_hash": log_content_hash(log_text) if log_text else "",
        "reviews_content_hash": reviews_content_hash(reviews or []) if reviews else "",
        "content_root_hash": content_root_hash(content_root),
    }


__all__ = [
    "normalize_markdown",
    "normalize_yaml_payload",
    "normalize_yaml_text",
    "validate_markdown_normalization",
    "validate_yaml_normalization",
    "metadata_hash",
    "plan_content_hash",
    "log_content_hash",
    "reviews_content_hash",
    "content_root_hash",
    "all_hashes",
]
