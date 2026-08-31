"""T5-B attribution layer (实验 e63ec33e I3)。

把 ``NormalizedUsage`` 事件流归到具体实验上。归因优先级按 plan §A3：

1. **inline_field**：raw_payload.message.experiment_id（最优先）
2. **file_window**：source_file 路径或 session_id 含 UUID 形式 experiment_id
3. **post_accept_continuation**：session 时间窗与 experiment.started_at/ended_at 重叠
   → first_experiment_id（按 started_at 升序取首个，避免重复计数）
4. **unmatched**：归入 unknown bucket（不丢，仅标记）

设计原则（plan §A3 + §A5）：
- **session 级归因**：一个 jsonl 文件 = 一个 session，session 内所有事件共享
  attribution（避免事件级抖动的 first_experiment_id 漂移）
- **matched_via 必填**：聚合输出 ``match_breakdown`` 据此统计 4 类比例
- **不丢 unmatched**：unknown bucket 单独可查，方便定位 session 跨实验边界

``ExperimentWindow`` 由 CLI 调用方传入（从 ``map experiment list`` 或 DB 拉），
本模块不直接调 MAP API（保持单文件无 IO 副作用，便于测试）。
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone

from cli.cost_ledger.layer1_collector import RawUsageEvent

_log = logging.getLogger("cli.cost_ledger.attribution")

# 4 类匹配路径常量（聚合输出 match_breakdown key 用）
MATCH_INLINE_FIELD = "inline_field"
MATCH_FILE_WINDOW = "file_window"
MATCH_POST_ACCEPT = "post_accept_continuation"
MATCH_UNMATCHED = "unmatched"

# UUID 格式正则（file_window 路径/session_id 提取用）
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExperimentWindow:
    """实验活跃时间窗（归因 post_accept_continuation 用）。

    ``started_at`` / ``ended_at`` 取 ISO 8601 字符串；``ended_at`` 为 None 表示
    实验仍在运行（处理时视 ``now`` 为右开区间）。
    """

    experiment_id: str
    started_at: str
    ended_at: str | None = None


@dataclass(frozen=True)
class SessionAttribution:
    """一个 session 的归因结果。

    ``experiment_id`` 为 None 表示 unmatched；``matched_via`` 记录归因路径。
    """

    experiment_id: str | None
    matched_via: str  # MATCH_* 之一
    session_id: str
    source_file: str


# =========================================================================
# 4 类匹配路径实现
# =========================================================================


def _match_inline_field(raw: RawUsageEvent) -> str | None:
    """inline_field：raw_payload.message.experiment_id 显式标记。"""
    msg = raw.raw_payload.get("message") or {}
    if not isinstance(msg, dict):
        return None
    eid = msg.get("experiment_id")
    return eid if isinstance(eid, str) and eid else None


def _match_file_window(raw: RawUsageEvent) -> str | None:
    """file_window：source_file 路径或 session_id 含 UUID 形式 experiment_id。

    典型场景：jsonl 文件名是 ``exp_<uuid>.jsonl``，或父目录含 uuid。

    **Real-data fix（T5-B I4 follow-up）**：session jsonl 文件名本身就是
    session UUID（如 ``a4e8f012-....jsonl``），朴素 ``_UUID_RE.search``
    会把 session_id 自身误识为 experiment_id 导致 100% false positive。
    修复：跳过与 ``raw.session_id`` 自身相等的 UUID，仅返回真正的 experiment UUID。
    """
    for src in (raw.source_file, raw.session_id):
        m = _UUID_RE.search(src)
        if not m:
            continue
        candidate = m.group(0).lower()
        # 排除 session_id 自身——session 文件名 UUID 不是 experiment_id
        if candidate == raw.session_id.lower():
            continue
        return candidate
    return None


def _parse_iso(ts: str) -> datetime | None:
    """Parse ISO 8601 字符串 → tz-aware UTC datetime；解析失败返回 None。

    Real-data fix（T5-B I4 follow-up）：legacy experiment.created_at 可能
    为 naive datetime（无 tzinfo），与 tz-aware session ts 比较会抛
    ``TypeError: can't compare offset-naive and offset-aware datetimes``。
    修复：naive 输入视作 UTC，与 aware 输入统一 tz-aware 表达。
    """
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        # naive 输入视作 UTC（与 MAP server 实际口径一致）
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _session_window(
    raw_events: list[RawUsageEvent],
) -> tuple[datetime | None, datetime | None]:
    """Compute (ts_min, ts_max) from a session's raw events; missing ts → None."""
    ts_list = [_parse_iso(r.ts) for r in raw_events if r.ts]
    ts_list = [t for t in ts_list if t is not None]
    if not ts_list:
        return None, None
    return min(ts_list), max(ts_list)


def _first_overlapping(
    session_window: tuple[datetime | None, datetime | None],
    experiments: Iterable[ExperimentWindow],
) -> str | None:
    """Return first_experiment_id whose window overlaps session_window.

    实验按 started_at 升序排序取首个；session 至少有一个 ts 落入区间算重叠。
    """
    ts_min, ts_max = session_window
    if ts_min is None:
        return None
    # Sort experiments by started_at ascending; take first overlapping
    sorted_exps = sorted(experiments, key=lambda e: e.started_at)
    for exp in sorted_exps:
        start = _parse_iso(exp.started_at)
        end = _parse_iso(exp.ended_at) if exp.ended_at else None
        if start is None:
            continue
        # session ts_min ≥ exp.start AND (exp.end is None OR session ts_max ≤ exp.end)
        if ts_min >= start and (end is None or ts_max is None or ts_max <= end):
            return exp.experiment_id
    return None


# =========================================================================
# Session 级归因主入口
# =========================================================================


def attribute_session(
    raw_events: list[RawUsageEvent],
    experiments: list[ExperimentWindow],
) -> SessionAttribution:
    """Attribute a single session (jsonl 文件) to one experiment.

    4 段优先级（plan §A3）：
    1. inline_field（任一 raw 显式标记 → 全 session 归该 experiment）
    2. file_window（路径/session_id 含 uuid → 全 session 归该 experiment）
    3. post_accept_continuation（session 时间窗与 experiment 重叠 → first_experiment_id）
    4. unmatched（以上都不命中）

    session 级而非 event 级：避免事件级抖动让 first_experiment_id 漂移；
    整个 jsonl 文件共享 attribution，聚合按 session 维度即可。
    """
    if not raw_events:
        raise ValueError("raw_events must be non-empty")

    first = raw_events[0]

    # 1. inline_field（任一事件显式标记即胜出）
    for r in raw_events:
        eid = _match_inline_field(r)
        if eid:
            return SessionAttribution(
                experiment_id=eid,
                matched_via=MATCH_INLINE_FIELD,
                session_id=first.session_id,
                source_file=first.source_file,
            )

    # 2. file_window（路径/session_id 含 uuid）
    for r in raw_events:
        eid = _match_file_window(r)
        if eid:
            return SessionAttribution(
                experiment_id=eid,
                matched_via=MATCH_FILE_WINDOW,
                session_id=first.session_id,
                source_file=first.source_file,
            )

    # 3. post_accept_continuation
    window = _session_window(raw_events)
    eid = _first_overlapping(window, experiments)
    if eid:
        return SessionAttribution(
            experiment_id=eid,
            matched_via=MATCH_POST_ACCEPT,
            session_id=first.session_id,
            source_file=first.source_file,
        )

    # 4. unmatched
    return SessionAttribution(
        experiment_id=None,
        matched_via=MATCH_UNMATCHED,
        session_id=first.session_id,
        source_file=first.source_file,
    )


def attribute_sessions(
    raw_sessions: Iterable[list[RawUsageEvent]],
    experiments: list[ExperimentWindow],
) -> Iterator[SessionAttribution]:
    """Stream-attribute multiple sessions; convenience for CLI 调用。

    每个 inner list = 一个 session（jsonl 文件的所有事件）。
    """
    for session_events in raw_sessions:
        if not session_events:
            continue
        yield attribute_session(session_events, experiments)


__all__ = [
    "ExperimentWindow",
    "SessionAttribution",
    "MATCH_INLINE_FIELD",
    "MATCH_FILE_WINDOW",
    "MATCH_POST_ACCEPT",
    "MATCH_UNMATCHED",
    "attribute_session",
    "attribute_sessions",
    "_match_inline_field",
    "_match_file_window",
    "_session_window",
    "_first_overlapping",
]
