"""迁移 manifest —— stale 语义六类（实验 M2 I6：A6）。

自 ``migration_manifest_service.py`` 拆出（模块 800 行上限，T46）。本模块
只有「失败原因分类」这一类纯判定逻辑，对宿主的唯一依赖（``mark_failed``）
走函数内 lazy import 反向引用，避免与宿主底部的 re-export 形成导入环。
宿主对本模块符号做 re-export，既有导入路径与 monkeypatch 面不变。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from server.domain.models import MigrationManifestItem

# ---------------------------------------------------------------------------
# stale 语义六类（实验 M2 I6：A6）
# ---------------------------------------------------------------------------
#
# execute 阶段每个 item apply 失败时，按失败原因归类到下面 6 类 stale code。
# 把分类抽到集中表里，理由：
#
# 1. host 运维 / reviewer 看 ``last_error`` 字段时需要稳定 code 才能 grep
#    / 写 alert 规则（"STALE_PAYLOAD_TOO_LARGE 触发就拒绝并通知"）。
# 2. 不同 code 对应不同修复路径（CAS 重试 vs 减 payload vs 改 publisher）；
#    分类驱动下一步动作。
# 3. 留扩展位：未来 server 端新增错误类型时，加一个新 code 即可，不需要
#    改 ``mark_failed`` / CLI 输出 / 文档。
#
# 实现：``classify_stale_code(exc)`` 走启发式（HTTP status + detail 关键词），
# 不依赖 server 端在 detail 里携带 ``code`` 字段（目前 server 错误格式不统一）。
# 启发式失败兜底为 ``STALE_OTHER``。

STALE_PROJECTION_REVISION_CONFLICT = "stale.projection_revision_conflict"
STALE_CONTENT_HASH_MISMATCH = "stale.content_hash_mismatch"
STALE_PUBLISHER_NOT_ALLOWED = "stale.publisher_not_allowed"
STALE_PAYLOAD_TOO_LARGE = "stale.payload_too_large"
STALE_WRITE_TOKEN_REPLAY = "stale.write_token_replay"
STALE_SCHEMA_MISMATCH = "stale.schema_mismatch"
STALE_OTHER = "stale.other"

STALE_ALL_CODES: tuple[str, ...] = (
    STALE_PROJECTION_REVISION_CONFLICT,
    STALE_CONTENT_HASH_MISMATCH,
    STALE_PUBLISHER_NOT_ALLOWED,
    STALE_PAYLOAD_TOO_LARGE,
    STALE_WRITE_TOKEN_REPLAY,
    STALE_SCHEMA_MISMATCH,
    STALE_OTHER,
)


def _exc_text(exc: BaseException | dict | str) -> str:
    """从异常 / dict / str 提取可分类的文本。"""
    if isinstance(exc, BaseException):
        return f"{type(exc).__name__}: {exc}".lower()
    if isinstance(exc, dict):
        # MAP API 错误通常 ``{"detail": "..."}`` 或 ``{"code": "...", "detail": "..."}``
        parts = []
        for k in ("code", "detail", "message", "error"):
            v = exc.get(k)
            if v:
                parts.append(str(v))
        return " ".join(parts).lower()
    return str(exc).lower()


def classify_stale_code(exc: BaseException | dict | str) -> str:
    """把 server 端异常 / API error body 归到 6 类 stale code 之一。

    分类规则（按优先级匹配；先命中先用）：

    1. ``projection revision conflict`` / ``base_revision`` → CAS
       revision 漂移（C3 触发频率最高，CAS retry 兜底）
    2. ``content hash mismatch`` / ``expected_hash`` → tombstone 拒绝
       （写端期望的 hash 与 server 端 projection 现存 hash 不一致）
    3. ``publisher not allowed`` / ``publisher_agent_id`` → 权限拒绝
       （走的是非 host/admin publisher）
    4. ``too large`` / ``payload size`` / ``exceeds`` / ``413`` → 容量超限
       （payload > ``_PROJECTION_MAX_BYTES`` 或单 object > ``_PROJECTION_MAX_OBJECT_BYTES``）
    5. ``token replay`` / ``write_token`` / ``nonce`` → 写 token 重放
       （fs_write_receipts 唯一索引命中）
    6. ``validation`` / ``schema`` / ``422`` / ``unprocessable`` → schema 失败
       （Pydantic validation）
    7. 其他 → ``STALE_OTHER``（兜底；后续分类细化时再加 code）
    """
    text = _exc_text(exc)

    # 1. CAS revision
    if any(
        kw in text
        for kw in (
            "projection revision conflict",
            "base_revision",
            "expected revision",
            "cas conflict",
        )
    ):
        return STALE_PROJECTION_REVISION_CONFLICT

    # 2. content hash mismatch (tombstone)
    if any(
        kw in text
        for kw in (
            "content hash mismatch",
            "expected_hash",
            "hash mismatch",
            "tombstone",
        )
    ):
        return STALE_CONTENT_HASH_MISMATCH

    # 3. publisher not allowed
    if any(
        kw in text
        for kw in (
            "publisher not allowed",
            "publisher_agent_id",
            "not in publisher allowlist",
            "forbidden publisher",
        )
    ):
        return STALE_PUBLISHER_NOT_ALLOWED

    # 4. payload too large
    if any(
        kw in text
        for kw in (
            "payload too large",
            "payload size",
            "exceeds maximum",
            " 413 ",
            "max_bytes",
            "max_topics",
        )
    ):
        return STALE_PAYLOAD_TOO_LARGE

    # 5. write token replay
    if any(
        kw in text
        for kw in (
            "write token replay",
            "token replay",
            "nonce already used",
            "write_token",
            "fs_write_receipts",
        )
    ):
        return STALE_WRITE_TOKEN_REPLAY

    # 6. schema mismatch
    if any(
        kw in text
        for kw in (
            "validation",
            "schema mismatch",
            " 422 ",
            "unprocessable",
            "validation error",
            "field required",
        )
    ):
        return STALE_SCHEMA_MISMATCH

    return STALE_OTHER


def mark_failed_with_stale(
    db: Session, *, item_id: str, error: str, exc: BaseException | dict | str
) -> MigrationManifestItem | None:
    """``mark_failed`` 的 stale-aware 变体：把分类 code 拼到 ``last_error`` 前缀。

    last_error 格式：``[<stale_code>] <原 error 文本>``。host / reviewer 工具
    按 ``]`` 前缀 grep 即可拿到 stable code，不需要重新跑分类。
    """
    from server.services import migration_manifest_service as _core
    code = classify_stale_code(exc)
    tagged = f"[{code}] {error}" if error else f"[{code}]"
    return _core.mark_failed(db, item_id=item_id, error=tagged)

