"""T5-B orchestrator (实验 e63ec33e I4)。

把 layer1 (scan) → layer2 (map) → attribution (归因) → render (聚合) 串成
端到端 pipeline，供 CLI 命令 ``map experiment show --cost`` /
``map waker costs --by-*`` 直接调用。

设计原则（plan §A4）：
- **复用 cli/cost_ledger/ 库**：lib/cost_ledger/ 留给跨场景共享；本模块只在
  cli 域串联 + 提供 CLI 友好输出（YAML dict）
- **session 级归因**：整 jsonl 文件所有 raw 事件共享同一 attribution，
  避免事件级抖动让 first_experiment_id 漂移
- **unmatched 显式保留**：归因失败的 session 不丢，写入 match_breakdown.unmatched
- **损坏行 warn 不崩**：layer1 已 warn；orchestrator 不重复 warn
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from cli.cost_ledger.attribution import (
    ExperimentWindow,
    SessionAttribution,
    attribute_session,
)
from cli.cost_ledger.layer1_collector import (
    RawUsageEvent,
    _project_jsonl_paths,
    scan_session_jsonl,
)
from cli.cost_ledger.layer2_mapper import NormalizedUsage, map_event
from cli.cost_ledger.render import (
    CostBreakdown,
    PersonaAggregate,
    PersonaCost,
    aggregate_by_experiment,
    aggregate_by_persona,
    match_breakdown,
)

_log = logging.getLogger("cli.cost_ledger.orchestrator")


@dataclass(frozen=True)
class AttributedSession:
    """一个 session 的归因结果 + 归一化事件。

    一个 jsonl 文件 = 一个 session；session 内所有 NormalizedUsage 共享同一
    SessionAttribution（避免事件级归因漂移）。
    """

    persona: str
    session_id: str
    source_file: str
    events: tuple[NormalizedUsage, ...]
    attribution: SessionAttribution


def _group_raw_events_by_session(
    project_root: Path,
) -> Iterator[tuple[str, str, list[RawUsageEvent]]]:
    """Yield ``(persona, source_file, [RawUsageEvent])`` per session (jsonl file).

    Source-level grouping：每个 jsonl 文件一个 session；layer1 按 (persona, file)
    yield，orchestrator 按 file 边界聚合。
    """
    for persona, jsonl_path in _project_jsonl_paths(project_root):
        events = list(scan_session_jsonl(jsonl_path, persona=persona))
        if not events:
            continue
        yield persona, str(jsonl_path), events


def collect_attributed_sessions(
    project_root: Path,
    experiments: list[ExperimentWindow],
) -> Iterator[AttributedSession]:
    """Scan → map → attribute per session; yield ``AttributedSession``。

    Output:
        每个 session（jsonl 文件）一个 AttributedSession；事件为
        ``NormalizedUsage``（layer2 归一化后），attribution 由整 session
        共享（layer3 session 级归因）。
    """
    for persona, source_file, raw_events in _group_raw_events_by_session(project_root):
        # 1. layer2：raw → normalized（事件级映射）
        normalized = tuple(map_event(r) for r in raw_events)
        if not normalized:
            continue
        # 2. layer3：session 级归因（基于 raw_events 提取 inline_field / file_window /
        #    时间窗；normalized 共享结果）
        attr = attribute_session(raw_events, experiments)
        yield AttributedSession(
            persona=persona,
            session_id=attr.session_id,
            source_file=source_file,
            events=normalized,
            attribution=attr,
        )


def _attributed_to_pairs(sessions: Iterable[AttributedSession]) -> Iterator[tuple[NormalizedUsage, SessionAttribution]]:
    """Flatten AttributedSessions → (NormalizedUsage, SessionAttribution) pairs.

    Render 层 ``aggregate_by_*`` 接受这种 pair 流。
    """
    for sess in sessions:
        for ev in sess.events:
            yield ev, sess.attribution


def render_experiment_view(
    project_root: Path,
    *,
    experiment_id: str,
    experiments: list[ExperimentWindow],
) -> CostBreakdown:
    """Render single-experiment CostBreakdown via orchestrator。

    Filter by attribution.experiment_id == experiment_id；unmatched 不计入
    本实验但保留在 match_breakdown.unmatched 桶。
    """
    sessions = list(collect_attributed_sessions(project_root, experiments))
    filtered: list[tuple[NormalizedUsage, SessionAttribution]] = []
    for sess in sessions:
        if sess.attribution.experiment_id != experiment_id:
            continue
        for ev in sess.events:
            filtered.append((ev, sess.attribution))
    return aggregate_by_experiment(iter(filtered), experiment_id=experiment_id)


def render_persona_aggregate_view(
    project_root: Path,
    *,
    experiments: list[ExperimentWindow],
) -> dict[str, PersonaAggregate]:
    """Render cross-experiment per-persona aggregate view."""
    sessions = collect_attributed_sessions(project_root, experiments)
    return aggregate_by_persona(_attributed_to_pairs(sessions))


def render_match_breakdown_view(
    project_root: Path,
    *,
    experiments: list[ExperimentWindow],
) -> dict[str, int]:
    """Render global match_breakdown 4-bucket counts."""
    sessions = collect_attributed_sessions(project_root, experiments)
    return match_breakdown(sess.attribution for sess in sessions)


def cost_breakdown_to_yaml_dict(
    breakdown: CostBreakdown,
    *,
    pricing_unavailable: bool = False,
    pricing_source_date: str | None = None,
) -> dict[str, object]:
    """Convert ``CostBreakdown`` to a YAML-friendly dict (plan §A5 输出示例)。"""
    return {
        "experiment_id": breakdown.experiment_id,
        "persona_breakdown": {
            persona: _persona_cost_to_dict(cost)
            for persona, cost in breakdown.persona_breakdown.items()
        },
        "session_kind_breakdown": {
            sk: _persona_cost_to_dict(cost)
            for sk, cost in breakdown.session_kind_breakdown.items()
        },
        "match_breakdown": dict(breakdown.match_breakdown),
        "sanity_warning": breakdown.sanity_warning,
        "pricing_unavailable": pricing_unavailable,
        "pricing_source_date": pricing_source_date,
    }


def persona_aggregate_to_yaml_dict(
    aggregate: dict[str, PersonaAggregate],
) -> dict[str, object]:
    """Convert PersonaAggregate dict to YAML-friendly nested dict."""
    return {
        persona: {
            "experiment_breakdown": {
                exp_id: _persona_cost_to_dict(cost)
                for exp_id, cost in agg.experiment_breakdown.items()
            },
            "total": _persona_cost_to_dict(agg.total) if agg.total is not None else None,
        }
        for persona, agg in aggregate.items()
    }


def _persona_cost_to_dict(cost: PersonaCost) -> dict[str, int]:
    return {
        "input_tokens": cost.input_tokens,
        "output_tokens": cost.output_tokens,
        "cache_creation_input_tokens": cost.cache_creation_input_tokens,
        "cache_read_input_tokens": cost.cache_read_input_tokens,
        "sessions": cost.sessions,
    }


__all__ = [
    "AttributedSession",
    "collect_attributed_sessions",
    "render_experiment_view",
    "render_persona_aggregate_view",
    "render_match_breakdown_view",
    "cost_breakdown_to_yaml_dict",
    "persona_aggregate_to_yaml_dict",
]
