"""T5-B Layer 2 mapper (实验 e63ec33e I2)。

把 ``RawUsageEvent``（cli/cost_ledger/layer1_collector.py 产出）按
``version + field_name`` dict 归一化成 ``NormalizedUsage``。缺字段显式
``unknown: {raw_field_name}`` 元组不静默丢弃（plan §A1 契约）；cache
字段（``cache_creation_input_tokens`` / ``cache_read_input_tokens``）
独立计数不并入 input（plan §A6 契约）。

设计原则：
- **采集器与映射器解耦**：I1 不假设字段名，I2 集中管 schema 版本演进
- **缺字段显式 unknown**：绝不默认 0 兜底（plan §A6 边界：缺价目只报 token +
  pricing_unavailable 标记，不允许 0 兜底）
- **cache 分桶**：cache_creation_input_tokens / cache_read_input_tokens 独立
  计数；聚合时分别累计

VERSION_FIELD_MAP 实测（spike_note.md 验证）：Claude Agent SDK 2.1.191 已用
``input_tokens`` 命名；旧版本可能叫 ``prompt_tokens``——映射表支持两种 schema
同时归一化。后续版本演进时只需在 VERSION_FIELD_MAP 加新版本子 dict。

输出 ``NormalizedUsage``：固定 schema（I3 attribution + I4 render 直接消费）；
``raw_field_name`` 写映射源字段（``input_tokens`` or ``prompt_tokens``），方便
A8 (a) case 校验「缺字段显式 unknown」契约。
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field

from cli.cost_ledger.layer1_collector import RawUsageEvent

_log = logging.getLogger("cli.cost_ledger.layer2_mapper")

# 标准字段名（所有版本归一化目标）
FIELD_INPUT = "input_tokens"
FIELD_OUTPUT = "output_tokens"
FIELD_CACHE_CREATION = "cache_creation_input_tokens"
FIELD_CACHE_READ = "cache_read_input_tokens"

# 现代 schema 默认表（Claude Agent SDK ≥2.x 实测）：
#   - "2.1.191" 实测（spike_note.md:26-48）用 ``input_tokens``
#   - "2.1.220" / "2.1.233" / "2.1.259" 实测（2026-09-15 本机 runtime home
#     全量 2435 条 assistant 行验证）字段名与 2.1.191 完全一致
#   - "2.1.277" 实测（2026-09-22 探针 token-cost-audit.py）字段名不变
# legacy schema：历史版本 ``prompt_tokens`` / ``completion_tokens`` 别名。
#
# 实验 bccb59ea A1：路由从「精确版本号键控」改为「现代默认表 + 字段名探测 +
# 版本例外覆盖」——SDK 升级（新版本号）不再让数据落入全 unknown 分支；
# 版本精确键仅作例外覆盖保留。fail-explicit 边界不变：usage 无任何可识别
# 字段名时仍全 unknown 兜底，绝不 0 填充、不静默猜测。
MODERN_FIELD_MAP: dict[str, str] = {
    FIELD_INPUT: "input_tokens",
    FIELD_OUTPUT: "output_tokens",
    FIELD_CACHE_CREATION: "cache_creation_input_tokens",
    FIELD_CACHE_READ: "cache_read_input_tokens",
}
LEGACY_FIELD_MAP: dict[str, str] = {
    FIELD_INPUT: "prompt_tokens",
    FIELD_OUTPUT: "completion_tokens",
    FIELD_CACHE_CREATION: "cache_creation_input_tokens",
    FIELD_CACHE_READ: "cache_read_input_tokens",
}
VERSION_FIELD_MAP: dict[str, dict[str, str]] = {
    "2.1.191": MODERN_FIELD_MAP,
    "2.1.220": MODERN_FIELD_MAP,
    "2.1.233": MODERN_FIELD_MAP,
    "2.1.259": MODERN_FIELD_MAP,
    "legacy": LEGACY_FIELD_MAP,  # 旧版本 alias 例外覆盖
}


@dataclass(frozen=True)
class NormalizedUsage:
    """归一化后的 token 计数。

    cache 字段独立（不并入 input）：plan §A6 硬约束。
    ``unknown_fields`` 记录 I1 输出中缺字段名集合；非空时由 I3/I4 报表
    发 ``pricing_unavailable`` 告警（不允许 0 兜底）。
    """

    persona: str
    session_id: str
    ts: str
    version: str
    source_file: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    # 缺字段显式 unknown tuple（field_name → raw_field_name 映射源；
    # 若 raw 字段名与目标同名则等于目标名）
    unknown_fields: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def is_complete(self) -> bool:
        """所有 4 个 token 字段都有值（无 unknown）。"""
        return (
            self.input_tokens is not None
            and self.output_tokens is not None
            and self.cache_creation_input_tokens is not None
            and self.cache_read_input_tokens is not None
        )


def _probe_field_map(usage: dict) -> dict[str, str] | None:
    """按 usage 字段名探测 schema（实验 bccb59ea A1）。

    规则（确定性，无猜测）：含任一现代字段名（``input_tokens`` /
    ``cache_read_input_tokens``）→ 现代表；否则含 ``prompt_tokens`` →
    legacy 表；两者皆非 → None（调用方全 unknown 兜底）。
    现代优先：两族字段同时存在（防御场景）按现代表解析。
    """
    if FIELD_INPUT in usage or FIELD_CACHE_READ in usage:
        return MODERN_FIELD_MAP
    if "prompt_tokens" in usage:
        return LEGACY_FIELD_MAP
    return None


def _resolve_field_map(
    version: str, usage: dict | None = None
) -> dict[str, str] | None:
    """两级路由：版本精确例外表 → 字段名探测 → None（unknown version）。

    - 精确键命中：与既有语义逐字节一致（五键不回归）。
    - 未命中且 ``usage`` 可用：字段名探测（SDK 升级新版本号不再全 unknown）。
    - 仍无法判定：None → 4 字段全 unknown（不兜底 0；plan §A1 契约保持，
      I4 报表据此发 WARN）。
    """
    exact = VERSION_FIELD_MAP.get(version)
    if exact is not None:
        return exact
    if usage:
        return _probe_field_map(usage)
    return None


def _extract_int(usage: dict, raw_field: str) -> int | None:
    """Extract int from ``usage[raw_field]``; 缺失或非整数返回 None。"""
    val = usage.get(raw_field)
    if val is None:
        return None
    if isinstance(val, bool):
        # bool 是 int 子类，但语义上不是 token 数；显式排除
        return None
    if isinstance(val, int):
        return val
    return None


def map_event(event: RawUsageEvent) -> NormalizedUsage:
    """Map one ``RawUsageEvent`` → ``NormalizedUsage``.

    缺字段：写 ``unknown_fields`` 元组；不静默丢弃（plan §A1 契约）。
    缺 usage 块：4 字段全 None + unknown_fields 含 4 项（I4 报表据此发 WARN）。
    """
    usage = event.raw_payload.get("message", {}).get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}

    field_map = _resolve_field_map(event.version, usage)
    if field_map is None:
        # version 未在映射表中 → 4 字段全 unknown（不兜底 0）
        return NormalizedUsage(
            persona=event.persona,
            session_id=event.session_id,
            ts=event.ts,
            version=event.version,
            source_file=event.source_file,
            unknown_fields=tuple(
                (target, "")
                for target in (
                    FIELD_INPUT,
                    FIELD_OUTPUT,
                    FIELD_CACHE_CREATION,
                    FIELD_CACHE_READ,
                )
            ),
        )

    unknown: list[tuple[str, str]] = []
    input_tokens = _extract_int(usage, field_map[FIELD_INPUT])
    if input_tokens is None:
        unknown.append((FIELD_INPUT, field_map[FIELD_INPUT]))
    output_tokens = _extract_int(usage, field_map[FIELD_OUTPUT])
    if output_tokens is None:
        unknown.append((FIELD_OUTPUT, field_map[FIELD_OUTPUT]))
    cache_creation = _extract_int(usage, field_map[FIELD_CACHE_CREATION])
    if cache_creation is None:
        unknown.append((FIELD_CACHE_CREATION, field_map[FIELD_CACHE_CREATION]))
    cache_read = _extract_int(usage, field_map[FIELD_CACHE_READ])
    if cache_read is None:
        unknown.append((FIELD_CACHE_READ, field_map[FIELD_CACHE_READ]))

    return NormalizedUsage(
        persona=event.persona,
        session_id=event.session_id,
        ts=event.ts,
        version=event.version,
        source_file=event.source_file,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
        unknown_fields=tuple(unknown),
    )


def map_events(events: Iterator[RawUsageEvent]) -> Iterator[NormalizedUsage]:
    """Convenience: map a stream of ``RawUsageEvent`` → ``NormalizedUsage``.

    主入口。CLI ``map experiment show --cost`` 经 lib/cost_ledger/aggregation.py
    调用本函数。
    """
    for ev in events:
        yield map_event(ev)


__all__ = [
    "NormalizedUsage",
    "VERSION_FIELD_MAP",
    "FIELD_INPUT",
    "FIELD_OUTPUT",
    "FIELD_CACHE_CREATION",
    "FIELD_CACHE_READ",
    "map_event",
    "map_events",
    "_resolve_field_map",
    "_extract_int",
]
