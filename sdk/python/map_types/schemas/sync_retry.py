"""Publish CAS 重试与幂等约束（实验 M2 A3 — I3 落地）。

publish（``fs_apply_projection_delta``）是单 publisher + revision CAS
语义：客户端先拉 inventory 拿 ``base_revision``，再带着这个 base 发
delta；服务端校验 ``payload.base_revision == row.revision``，否则
``409 fs_projection_conflict`` 拒收。

**重试语义（A3 幂等重试）**：当两个客户端在同一 base 上并发 publish
时，先到的赢，后到的 409——后到者只需重新拉 inventory（新 base）、
重建 delta、同 input 重试即可，不需要改本地内容，也不需要通知用户
「失败了」。本模块提供 ``apply_delta_with_retry`` 把这个循环封装
成单调用：传 ``build_payload(base_revision)`` 闭包（每次重试用
新 base 重算 ``changes`` 与 ``result_content_hash``），helper 自
动 cap 在 ``MAX_DELTA_RETRIES`` 次；耗尽后 raise ``RetryableCASConflict``。

**「缺字段 ≠ 删除」不变量（reviewer 建议 + A3 不变量）**：

- 服务端 ``apply_fs_projection_delta`` 对 ``*_upsert`` 是**整体替换**
  （``topics[slug] = change.value``）—— 客户端必须发完整对象；缺
  字段会以 schema 默认值（``executor=""`` / ``topic=""`` /
  ``description=""``）覆盖既有值。这是「缺字段 = 隐式删除字段值」
  的合约。
- 真正的删除必须走 ``*_delete`` tombstone（``expected_hash`` 校验）
  —— upsert 永远不会删条目本身；即使 ``FsExperimentRead`` 全字段
  默认（``slug="x"`` 之外全空）发 upsert，服务端条目仍在，只
  是字段值被重置为默认。
- 本模块**不**改上述服务端合约；它只关心 CAS 重试面。

公开 API：

- 常量：``MAX_DELTA_RETRIES``
- 异常：``RetryableCASConflict``（继承 ``MAPConflictError`` 兼
  容 ``except MAPHTTPError``）
- 函数：``apply_delta_with_retry(client, pid, build_payload)``
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from map_client.exceptions import MAPConflictError, MAPHTTPError

from map_types.schemas.fs import FsProjectionDeltaRequest, FsProjectionDeltaResult

logger = logging.getLogger(__name__)

# A3 契约：连续失败 N 次后停止重试（避免无限循环 / 拖死调用方）。
# 3 次覆盖典型并发场景（一次首发 + 两次被抢），再多属于异常运维情形。
MAX_DELTA_RETRIES: int = 3


class RetryableCASConflict(MAPConflictError):
    """CAS 重试耗尽。

    携带 ``attempts`` / ``last_base_revision`` 便于 CLI 兜底展示。
    ``status_code=409`` / ``error_code="fs_projection_conflict"`` 兼容
    ``except MAPHTTPError`` / ``except MAPConflictError``。
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        last_base_revision: int | None,
        last_detail: str | None = None,
    ) -> None:
        super().__init__(
            status_code=409,
            detail=message,
            error_code="fs_projection_conflict",
        )
        self.attempts = attempts
        self.last_base_revision = last_base_revision
        self.last_detail = last_detail


def _is_retryable_409(exc: MAPHTTPError) -> bool:
    """判断 409 是否属于「CAS 冲突」（其他 409 —— e.g. publisher 冲突 —— 不重试）。

    判定规则（A3 不变量）：只有 ``detail`` 含 "projection revision
    conflict" 才视为可重试 CAS。publisher 冲突（"bound to another"）
    / 内容冲突（"result_content_hash does not match"）属于客户端错误，
    重试无用，必须人工介入。
    """
    if exc.status_code != 409:
        return False
    detail = exc.detail or ""
    return "projection revision conflict" in detail


def apply_delta_with_retry(
    client: Any,
    pid: uuid.UUID,
    build_payload: Callable[[int | None], FsProjectionDeltaRequest],
) -> FsProjectionDeltaResult:
    """CAS delta 应用 with idempotent retry on 409.

    Parameters
    ----------
    client:
        MAPClient duck-typed（暴露 ``fs_projection_inventory(pid)`` 和
        ``fs_apply_projection_delta(pid, payload)``）
    pid:
        project id
    build_payload:
        ``Callable[[base_revision], FsProjectionDeltaRequest]``——接受
        当前 base_revision（首次为 None），返回新 payload。**每次重试
        都会调用一次**（必须用新 base 重建 ``changes`` 与
        ``result_content_hash``，因为本地 FS 没变，但服务端 projection
        已经推进）。

    Returns
    -------
    FsProjectionDeltaResult — 服务端最后一次 apply 的返回值。

    Raises
    ------
    RetryableCASConflict:
        ``MAX_DELTA_RETRIES`` 次重试后仍 409（典型为长时并发 / 持续
        冲突）。携带 attempts / last_base_revision 字段供兜底展示。
    MAPHTTPError:
        非 409 / 非 CAS 错误（如 publisher 冲突 / 鉴权失败 / 服务器
        错误）直接抛出，不重试。
    """
    base_revision: int | None = None
    last_exc: MAPHTTPError | None = None
    for attempt in range(1, MAX_DELTA_RETRIES + 1):
        payload = build_payload(base_revision)
        try:
            return client.fs_apply_projection_delta(pid, payload)
        except MAPHTTPError as exc:
            if not _is_retryable_409(exc):
                # 非 CAS 冲突（publisher 冲突、validation 等）—— 不重试
                raise
            last_exc = exc
            # 重新拉 inventory 拿新 base_revision
            inventory = client.fs_projection_inventory(pid)
            new_base = None if inventory is None else inventory.projection_revision
            if new_base == base_revision:
                # base 没变 → 不是普通 CAS 推进，是持续冲突；最后一次也不重试
                logger.warning(
                    "CAS retry: base unchanged (%s) after 409; giving up",
                    new_base,
                )
                break
            base_revision = new_base
            logger.info(
                "CAS retry: 409 on attempt %d/%d, refetched base_revision=%s",
                attempt,
                MAX_DELTA_RETRIES,
                base_revision,
            )
    raise RetryableCASConflict(
        f"projection CAS conflict persists after {MAX_DELTA_RETRIES} retries",
        attempts=MAX_DELTA_RETRIES,
        last_base_revision=base_revision,
        last_detail=last_exc.detail if last_exc else None,
    )


__all__ = [
    "MAX_DELTA_RETRIES",
    "RetryableCASConflict",
    "apply_delta_with_retry",
]
