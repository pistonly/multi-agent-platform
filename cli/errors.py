"""CLI 通用错误类型。

T21（2026-08）：``WorkerError`` 原定义在 ``cli/host_worker_types.py``
（随 host-worker 整套退役后该模块仅剩此类型仍有 13+ 处引用），迁至
本模块独立成家；其余 protocol/config/dataclass 类型已随 host-worker
删除，无引用。
"""

from __future__ import annotations


class WorkerError(RuntimeError):
    """Worker 循环内可恢复/不可恢复错误的统一信封。"""
