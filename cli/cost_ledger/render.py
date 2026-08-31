"""T5-B render layer (实验 e63ec33e I4)。

聚合 ``NormalizedUsage`` 流 + ``SessionAttribution`` 结果成可读视图：

- ``aggregate_by_experiment(events, attributions, experiment_id)``：
  单实验 persona × session_kind 二维 + match_breakdown + sanity check
- ``aggregate_by_persona(events, attributions)``：跨实验 per-persona 汇总
- ``match_breakdown(attributions)``：4 类归因路径 session 数（plan §A5）
- ``sanity_check_assistant_vs_result(assistant, result)``：
  assistant 累加 ≈ result（差 < 1% pass，> 1% WARN），plan §A2 / I6

设计原则（plan §A5-A7）：
- **match_breakdown 默认输出**：不需要 ``--verbose`` flag，监督者一眼能定位误归
- **cache 字段独立**：cache_creation + cache_read 分别累计，不并入 input
- **缺价目只报 token**：``pricing_unavailable: true`` 标记，**不允许** 0 兜底
- **损坏行 warn 不崩**：上游 layer1/2 已 warn；render 仅聚合，不重复 warn
"""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from cli.cost_ledger.attribution import (
    MATCH_FILE_WINDOW,
    MATCH_INLINE_FIELD,
    MATCH_POST_ACCEPT,
    MATCH_UNMATCHED,
    SessionAttribution,
)
from cli.cost_ledger.layer2_mapper import NormalizedUsage

_log = logging.getLogger("cli.cost_ledger.render")

# session_kind 枚举（plan §B2 护栏：assistant + result 双采集）
SESSION_KIND_ASSISTANT = "assistant"
SESSION_KIND_RESULT = "result"

# sanity check 阈值（plan §A2：差 < 1% pass，> 1% WARN）
SANITY_DIFF_THRESHOLD = 0.01


@dataclass(frozen=True)
class PersonaCost:
    """单 persona 在某 scope（experiment 或跨实验）的 token 累计。"""

    persona: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    sessions: int = 0


@dataclass(frozen=True)
class CostBreakdown:
    """单实验 cost 视图（plan §A4 + §A5 默认输出）。"""

    experiment_id: str
    persona_breakdown: dict[str, PersonaCost] = field(default_factory=dict)
    session_kind_breakdown: dict[str, PersonaCost] = field(default_factory=dict)
    match_breakdown: dict[str, int] = field(default_factory=dict)
    sanity_warning: str | None = None
    pricing_unavailable: bool = False
    pricing_source_date: str | None = None


@dataclass(frozen=True)
class PersonaAggregate:
    """跨实验 per-persona 汇总。"""

    persona: str
    experiment_breakdown: dict[str, PersonaCost] = field(default_factory=dict)
    total: PersonaCost | None = None


# =========================================================================
# 聚合核心
# =========================================================================


def _accumulate(
    current: PersonaCost,
    usage: NormalizedUsage,
    *,
    session_inc: int = 0,
) -> PersonaCost:
    """Add one NormalizedUsage into PersonaCost accumulator.

    cache 字段独立（plan §A6），缺字段时 None 视作 0（render 层兜底，
    上游 layer2 已显式 unknown 标记）。
    """
    return PersonaCost(
        persona=current.persona,
        input_tokens=current.input_tokens + (usage.input_tokens or 0),
        output_tokens=current.output_tokens + (usage.output_tokens or 0),
        cache_creation_input_tokens=current.cache_creation_input_tokens + (usage.cache_creation_input_tokens or 0),
        cache_read_input_tokens=current.cache_read_input_tokens + (usage.cache_read_input_tokens or 0),
        sessions=current.sessions + session_inc,
    )


def _empty_persona(persona: str) -> PersonaCost:
    return PersonaCost(persona=persona)


def _increment_sessions(current: PersonaCost) -> PersonaCost:
    """Bump sessions by 1 without touching tokens (避免 _accumulate 二次累加)。"""
    return PersonaCost(
        persona=current.persona,
        input_tokens=current.input_tokens,
        output_tokens=current.output_tokens,
        cache_creation_input_tokens=current.cache_creation_input_tokens,
        cache_read_input_tokens=current.cache_read_input_tokens,
        sessions=current.sessions + 1,
    )


def _resolve_event_session_kind(event: NormalizedUsage) -> str:
    """Return session_kind from NormalizedUsage fields; default 'assistant'。

    Claude Agent SDK assistant 行 session_kind=assistant；result 行
    （final result payload）session_kind=result。本实验默认仅采 assistant
    （layer1 过滤），result 由后续 I6 实测补；这里兜底为 assistant。
    """
    # NormalizedUsage 当前不带 session_kind 字段；layer1 仅 yield assistant
    # 行。如果未来扩 result 双采集，可从 event_type / payload 字段派生。
    return SESSION_KIND_ASSISTANT


# =========================================================================
# match_breakdown + sanity check
# =========================================================================


def match_breakdown(
    attributions: Iterable[SessionAttribution],
) -> dict[str, int]:
    """Count sessions per matched_via path (plan §A5)。"""
    counter: Counter[str] = Counter()
    for attr in attributions:
        counter[attr.matched_via] += 1
    # 固定 4 桶 key（即使为 0）—— plan §A5 默认输出稳定 schema
    return {
        MATCH_INLINE_FIELD: counter.get(MATCH_INLINE_FIELD, 0),
        MATCH_FILE_WINDOW: counter.get(MATCH_FILE_WINDOW, 0),
        MATCH_POST_ACCEPT: counter.get(MATCH_POST_ACCEPT, 0),
        MATCH_UNMATCHED: counter.get(MATCH_UNMATCHED, 0),
    }


def sanity_check_assistant_vs_result(
    *,
    assistant_tokens: int,
    result_tokens: int,
) -> str | None:
    """Compare assistant vs result; return warning if diff > 1% (plan §A2 / I6)。

    Returns None if pass; returns warning string if diff > 1%。
    当 result_tokens=0（未采集 result）→ 不触发 WARN（避免 0 兜底噪音）。
    """
    if result_tokens <= 0:
        return None
    diff_ratio = abs(assistant_tokens - result_tokens) / result_tokens
    if diff_ratio > SANITY_DIFF_THRESHOLD:
        return (
            f"sanity check WARN: assistant={assistant_tokens} vs result={result_tokens} "
            f"diff={diff_ratio:.2%} > 1% threshold"
        )
    return None


# =========================================================================
# 主入口：单实验 + 跨实验聚合
# =========================================================================


def aggregate_by_experiment(
    events: Iterable[tuple[NormalizedUsage, SessionAttribution]],
    *,
    experiment_id: str,
) -> CostBreakdown:
    """Aggregate per-experiment cost view (plan §A4 + §A5)。

    Input: ``(NormalizedUsage, SessionAttribution)`` pairs — caller groups
    by experiment first (using ``attribution.experiment_id == experiment_id``)。

    Output: ``CostBreakdown`` 含 persona_breakdown + session_kind_breakdown
    + match_breakdown + sanity_warning。
    """
    # Materialize events once (render iterates 3 times; generator exhaustion bug 防护)
    events_list = list(events)
    persona_acc: dict[str, PersonaCost] = {}
    sk_acc: dict[str, PersonaCost] = {}

    for usage, _attr in events_list:
        # session_kind 二维
        sk = _resolve_event_session_kind(usage)
        sk_acc[sk] = _accumulate(sk_acc.get(sk, _empty_persona(sk)), usage)

        # persona 二维（归因的 session 共享 attribution）
        persona = usage.persona
        persona_acc[persona] = _accumulate(
            persona_acc.get(persona, _empty_persona(persona)),
            usage,
            session_inc=0,  # session_inc 仅在 attribution 一次性计入
        )

    # sessions per persona：每个 (persona, session_id) 仅 +1（不再累加 tokens，
    # 避免与上方 _accumulate 调用双重计数）
    seen_personas_per_session: set[tuple[str, str]] = set()  # (persona, session_id)
    for usage, attr in events_list:
        key = (usage.persona, attr.session_id)
        if key in seen_personas_per_session:
            continue
        seen_personas_per_session.add(key)
        persona = usage.persona
        persona_acc[persona] = _increment_sessions(persona_acc[persona])

    # match_breakdown 4 桶
    match_counter = Counter()
    seen_sessions: set[str] = set()
    for _, attr in events_list:
        if attr.session_id in seen_sessions:
            continue
        seen_sessions.add(attr.session_id)
        match_counter[attr.matched_via] += 1
    match_dict = {
        MATCH_INLINE_FIELD: match_counter.get(MATCH_INLINE_FIELD, 0),
        MATCH_FILE_WINDOW: match_counter.get(MATCH_FILE_WINDOW, 0),
        MATCH_POST_ACCEPT: match_counter.get(MATCH_POST_ACCEPT, 0),
        MATCH_UNMATCHED: match_counter.get(MATCH_UNMATCHED, 0),
    }

    # sanity check (assistant ≈ result)
    assistant_cost = sk_acc.get(SESSION_KIND_ASSISTANT, _empty_persona(SESSION_KIND_ASSISTANT))
    result_cost = sk_acc.get(SESSION_KIND_RESULT, _empty_persona(SESSION_KIND_RESULT))
    sanity_warn = sanity_check_assistant_vs_result(
        assistant_tokens=assistant_cost.input_tokens + assistant_cost.output_tokens,
        result_tokens=result_cost.input_tokens + result_cost.output_tokens,
    )

    return CostBreakdown(
        experiment_id=experiment_id,
        persona_breakdown=dict(persona_acc),
        session_kind_breakdown=dict(sk_acc),
        match_breakdown=match_dict,
        sanity_warning=sanity_warn,
        pricing_unavailable=False,  # 价目表由 I7 接入；render 默认 false
        pricing_source_date=None,
    )


def aggregate_by_persona(
    events: Iterable[tuple[NormalizedUsage, SessionAttribution]],
) -> dict[str, PersonaAggregate]:
    """Aggregate cross-experiment per-persona summary (plan §A4)。"""
    # Materialize events once (function iterates 3 times; generator exhaustion bug 防护)
    events_list = list(events)
    exp_per_persona: dict[str, dict[str, PersonaCost]] = {}
    for usage, attr in events_list:
        if attr.experiment_id is None:
            continue  # unmatched 不归任何 experiment
        persona = usage.persona
        exp_id = attr.experiment_id
        exp_per_persona.setdefault(persona, {}).setdefault(exp_id, _empty_persona(persona))

    # 二次循环累加 tokens + session
    session_keys: set[tuple[str, str, str]] = set()  # (persona, experiment_id, session_id)
    for usage, attr in events_list:
        if attr.experiment_id is None:
            continue
        key = (usage.persona, attr.experiment_id, attr.session_id)
        session_keys.add(key)

    for usage, attr in events_list:
        if attr.experiment_id is None:
            continue
        persona = usage.persona
        exp_id = attr.experiment_id
        exp_per_persona[persona][exp_id] = _accumulate(
            exp_per_persona[persona][exp_id], usage
        )

    # session_inc：每个 (persona, exp, session) 一次性（不再触发 tokens 累加）
    for persona, exp_id, _session_id in session_keys:
        exp_per_persona[persona][exp_id] = _increment_sessions(exp_per_persona[persona][exp_id])

    # 汇总 total
    result: dict[str, PersonaAggregate] = {}
    for persona, exp_map in exp_per_persona.items():
        total = PersonaCost(persona=persona)
        for exp_cost in exp_map.values():
            total = PersonaCost(
                persona=persona,
                input_tokens=total.input_tokens + exp_cost.input_tokens,
                output_tokens=total.output_tokens + exp_cost.output_tokens,
                cache_creation_input_tokens=total.cache_creation_input_tokens + exp_cost.cache_creation_input_tokens,
                cache_read_input_tokens=total.cache_read_input_tokens + exp_cost.cache_read_input_tokens,
                sessions=total.sessions + exp_cost.sessions,
            )
        result[persona] = PersonaAggregate(
            persona=persona,
            experiment_breakdown=exp_map,
            total=total,
        )
    return result


__all__ = [
    "SESSION_KIND_ASSISTANT",
    "SESSION_KIND_RESULT",
    "SANITY_DIFF_THRESHOLD",
    "PersonaCost",
    "CostBreakdown",
    "PersonaAggregate",
    "match_breakdown",
    "sanity_check_assistant_vs_result",
    "aggregate_by_experiment",
    "aggregate_by_persona",
    "_accumulate",
    "_empty_persona",
    "_increment_sessions",
    "_resolve_event_session_kind",
]
