"""Shared timezone helpers（T19 从三处雷同实现收敛）。

此前 ``status_service._as_utc``、``todo_service.list_stale_open_topics``
内联 ``_as_utc`` 与 stalled-lock 扫描的 ``_aware`` 三处各自实现「naive
补 UTC、aware 归一化」；收敛为一个 util，语义取并集：None 直通、naive
补 tzinfo、aware 一律 astimezone 到 UTC（DB 值本就按 UTC 存，转换不改
变比较结果）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import overload


@overload
def as_utc(value: datetime) -> datetime: ...


@overload
def as_utc(value: datetime | None) -> datetime | None: ...


def as_utc(value: datetime | None) -> datetime | None:
    """Normalize a datetime to aware-UTC; ``None`` passes through.

    Overloads keep ``datetime -> datetime`` precise so strict-mypy callers
    can compare returns directly without Optional narrowing.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
