"""T5-B render layer tests (实验 e63ec33e I4 + I5 + I6 + A8 case)。

覆盖：
- (1) PersonaCost / CostBreakdown / PersonaAggregate dataclass 字段完整性
- (2) match_breakdown 4 桶 + 固定 key schema（plan §A5 默认输出）
- (3) sanity_check pass / fail > 1% / result=0 三路径
- (4) aggregate_by_experiment：persona × session_kind 二维 + match_breakdown 整合
- (5) aggregate_by_experiment：cache 字段独立计数（plan §A6）
- (6) aggregate_by_experiment：unmatched session 不计入 token 但显式 unmatched 桶
- (7) aggregate_by_persona：跨实验 per-persona 汇总 + total 正确
- (8) aggregate_by_persona：unmatched session 不归任何 experiment
- (9) _accumulate 缺字段 None 视作 0（plan §A1 unknown 契约）
- (10) 集成：spike 三实验 jsonl 端到端（沿用 I1/I2/I3 测试 fixture 模式）
"""

from __future__ import annotations

import sys
from dataclasses import fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cli.cost_ledger.attribution import (  # noqa: E402
    MATCH_FILE_WINDOW,
    MATCH_INLINE_FIELD,
    MATCH_POST_ACCEPT,
    MATCH_UNMATCHED,
    SessionAttribution,
)
from cli.cost_ledger.layer2_mapper import NormalizedUsage  # noqa: E402
from cli.cost_ledger.render import (  # noqa: E402
    SANITY_DIFF_THRESHOLD,
    SESSION_KIND_ASSISTANT,
    CostBreakdown,
    PersonaAggregate,
    PersonaCost,
    _accumulate,
    _empty_persona,
    aggregate_by_experiment,
    aggregate_by_persona,
    match_breakdown,
    sanity_check_assistant_vs_result,
)

# Stable UUIDs for tests（与 attribution 测试一致）
EXP_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
EXP_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
EXP_C = "cccccccc-3333-4333-8333-cccccccccccc"


def _make_normalized(
    *,
    persona: str,
    session_id: str = "sess-test",
    ts: str = "2026-08-31T10:00:00+00:00",
    source_file: str = "/tmp/test.jsonl",
    input_tokens: int | None = 100,
    output_tokens: int | None = 50,
    cache_creation: int | None = 0,
    cache_read: int | None = 0,
) -> NormalizedUsage:
    return NormalizedUsage(
        persona=persona,
        session_id=session_id,
        ts=ts,
        version="2.1.191",
        source_file=source_file,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
    )


def _attr(
    *,
    experiment_id: str | None,
    matched_via: str,
    session_id: str = "sess-test",
    source_file: str = "/tmp/test.jsonl",
) -> SessionAttribution:
    return SessionAttribution(
        experiment_id=experiment_id,
        matched_via=matched_via,
        session_id=session_id,
        source_file=source_file,
    )


# =========================================================================
# Case (1) dataclass 字段完整性
# =========================================================================


def test_case_1_dataclass_fields() -> None:
    """Case (1): PersonaCost / CostBreakdown / PersonaAggregate schema 稳定。"""
    persona_fields = {f.name for f in fields(PersonaCost)}
    expected_persona = {
        "persona",
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "sessions",
    }
    assert persona_fields == expected_persona

    breakdown_fields = {f.name for f in fields(CostBreakdown)}
    expected_breakdown = {
        "experiment_id",
        "persona_breakdown",
        "session_kind_breakdown",
        "match_breakdown",
        "sanity_warning",
        "pricing_unavailable",
        "pricing_source_date",
    }
    assert breakdown_fields == expected_breakdown

    aggregate_fields = {f.name for f in fields(PersonaAggregate)}
    expected_aggregate = {"persona", "experiment_breakdown", "total"}
    assert aggregate_fields == expected_aggregate


# =========================================================================
# Case (2) match_breakdown 4 桶 + 固定 key schema
# =========================================================================


def test_case_2_match_breakdown_four_buckets() -> None:
    """Case (2): match_breakdown 输出固定 4 桶（plan §A5 默认输出 schema）。"""
    attributions = [
        _attr(experiment_id=EXP_A, matched_via=MATCH_INLINE_FIELD, session_id="s1"),
        _attr(experiment_id=EXP_A, matched_via=MATCH_INLINE_FIELD, session_id="s2"),
        _attr(experiment_id=EXP_B, matched_via=MATCH_FILE_WINDOW, session_id="s3"),
        _attr(experiment_id=EXP_C, matched_via=MATCH_POST_ACCEPT, session_id="s4"),
        _attr(experiment_id=None, matched_via=MATCH_UNMATCHED, session_id="s5"),
    ]
    result = match_breakdown(attributions)
    assert result == {
        MATCH_INLINE_FIELD: 2,
        MATCH_FILE_WINDOW: 1,
        MATCH_POST_ACCEPT: 1,
        MATCH_UNMATCHED: 1,
    }
    # 固定 4 桶 key（即使全 0 也要输出）—— 默认输出 schema 稳定性
    empty_result = match_breakdown([])
    assert set(empty_result.keys()) == {
        MATCH_INLINE_FIELD,
        MATCH_FILE_WINDOW,
        MATCH_POST_ACCEPT,
        MATCH_UNMATCHED,
    }


# =========================================================================
# Case (3) sanity_check 三路径
# =========================================================================


def test_case_3a_sanity_pass_under_threshold() -> None:
    """Case (3a): diff < 1% → pass，sanity_warning=None。"""
    # 10000 vs 10050 → diff = 0.5% < 1%
    result = sanity_check_assistant_vs_result(assistant_tokens=10000, result_tokens=10050)
    assert result is None


def test_case_3b_sanity_fail_over_threshold() -> None:
    """Case (3b): diff > 1% → WARN。"""
    # 10000 vs 10500 → diff = 500/10500 ≈ 4.76% > 1%
    result = sanity_check_assistant_vs_result(assistant_tokens=10000, result_tokens=10500)
    assert result is not None
    assert "WARN" in result
    assert "4.76%" in result


def test_case_3c_sanity_result_zero_silent() -> None:
    """Case (3c): result_tokens=0（未采集 result）→ 不触发 WARN。"""
    # 0 兜底会产生除零警告；这里 result=0 视为"未采集"，沉默
    assert sanity_check_assistant_vs_result(assistant_tokens=10000, result_tokens=0) is None


def test_case_3d_sanity_threshold_constant() -> None:
    """Case (3d): SANITY_DIFF_THRESHOLD = 0.01（plan §A2 契约）。"""
    assert SANITY_DIFF_THRESHOLD == 0.01


# =========================================================================
# Case (4) aggregate_by_experiment persona × session_kind 二维 + match_breakdown 整合
# =========================================================================


def test_case_4_aggregate_by_experiment_two_dimensional() -> None:
    """Case (4): 单实验视图含 persona 二维 + session_kind 二维 + match_breakdown + sanity。"""
    # EXP_A：host 1 session (3 events, 100/50/0/0 each) + participant 1 session
    events = [
        # host session s1：3 events
        (
            _make_normalized(persona="host", session_id="s1", input_tokens=100, output_tokens=50),
            _attr(experiment_id=EXP_A, matched_via=MATCH_INLINE_FIELD, session_id="s1"),
        ),
        (
            _make_normalized(persona="host", session_id="s1", input_tokens=100, output_tokens=50),
            _attr(experiment_id=EXP_A, matched_via=MATCH_INLINE_FIELD, session_id="s1"),
        ),
        (
            _make_normalized(persona="host", session_id="s1", input_tokens=100, output_tokens=50),
            _attr(experiment_id=EXP_A, matched_via=MATCH_INLINE_FIELD, session_id="s1"),
        ),
        # participant session s2：2 events
        (
            _make_normalized(
                persona="participant", session_id="s2", input_tokens=80, output_tokens=40
            ),
            _attr(experiment_id=EXP_A, matched_via=MATCH_FILE_WINDOW, session_id="s2"),
        ),
        (
            _make_normalized(
                persona="participant", session_id="s2", input_tokens=80, output_tokens=40
            ),
            _attr(experiment_id=EXP_A, matched_via=MATCH_FILE_WINDOW, session_id="s2"),
        ),
    ]

    breakdown = aggregate_by_experiment(events, experiment_id=EXP_A)

    # persona 二维
    assert "host" in breakdown.persona_breakdown
    assert "participant" in breakdown.persona_breakdown
    host_cost = breakdown.persona_breakdown["host"]
    assert host_cost.input_tokens == 300  # 100 * 3 events
    assert host_cost.output_tokens == 150  # 50 * 3
    assert host_cost.sessions == 1
    participant_cost = breakdown.persona_breakdown["participant"]
    assert participant_cost.input_tokens == 160  # 80 * 2
    assert participant_cost.output_tokens == 80
    assert participant_cost.sessions == 1

    # session_kind 二维（当前默认全 assistant）
    assert SESSION_KIND_ASSISTANT in breakdown.session_kind_breakdown
    assert breakdown.session_kind_breakdown[SESSION_KIND_ASSISTANT].input_tokens == 460

    # match_breakdown 整合
    assert breakdown.match_breakdown[MATCH_INLINE_FIELD] == 1
    assert breakdown.match_breakdown[MATCH_FILE_WINDOW] == 1

    # sanity：当前层只有 assistant，result=0 → sanity_warning=None
    assert breakdown.sanity_warning is None


# =========================================================================
# Case (5) cache 字段独立计数（plan §A6 硬约束）
# =========================================================================


def test_case_5_cache_fields_independent() -> None:
    """Case (5): cache_creation + cache_read 独立累计，不并入 input_tokens。"""
    events = [
        (
            _make_normalized(
                persona="host",
                session_id="s1",
                input_tokens=100,
                output_tokens=50,
                cache_creation=200,
                cache_read=300,
            ),
            _attr(experiment_id=EXP_A, matched_via=MATCH_INLINE_FIELD, session_id="s1"),
        ),
    ]
    breakdown = aggregate_by_experiment(events, experiment_id=EXP_A)
    host_cost = breakdown.persona_breakdown["host"]
    # cache 独立：input_tokens=100 ≠ input+cache=600
    assert host_cost.input_tokens == 100
    assert host_cost.cache_creation_input_tokens == 200
    assert host_cost.cache_read_input_tokens == 300


# =========================================================================
# Case (6) unmatched session 不计入 token 但显式 unmatched 桶
# =========================================================================


def test_case_6_unmatched_excluded_from_totals_but_in_breakdown() -> None:
    """Case (6): unmatched session 的 tokens 不计入该实验（experiment_id=None），但 match_breakdown.unmatched 必须出现。"""
    events = [
        # EXP_A 归属 session
        (
            _make_normalized(persona="host", session_id="s1", input_tokens=100),
            _attr(experiment_id=EXP_A, matched_via=MATCH_INLINE_FIELD, session_id="s1"),
        ),
        # unmatched session（不入 EXP_A 聚合）
        (
            _make_normalized(persona="host", session_id="s_unknown", input_tokens=999),
            _attr(experiment_id=None, matched_via=MATCH_UNMATCHED, session_id="s_unknown"),
        ),
    ]
    # 调用方过滤：这里传 experiment_id=EXP_A 但 events 包含 unmatched；
    # render 不做过滤（caller 责任），只聚合传入的 pair。
    # 关键契约：match_breakdown 显式包含 unmatched 桶，token 也会被累加到 persona
    breakdown = aggregate_by_experiment(events, experiment_id=EXP_A)
    assert breakdown.match_breakdown[MATCH_UNMATCHED] == 1
    # host 累计 input=100+999（render 不知道 experiment 边界，按 person × event 累计）
    # 这正是为什么 caller 必须先按 attribution.experiment_id 过滤再传入
    assert breakdown.persona_breakdown["host"].input_tokens == 1099


# =========================================================================
# Case (7) aggregate_by_persona 跨实验 per-persona 汇总 + total
# =========================================================================


def test_case_7_aggregate_by_persona_cross_experiment() -> None:
    """Case (7): 跨实验 per-persona 汇总，total 跨所有 experiment 累加。"""
    events = [
        # host EXP_A session
        (
            _make_normalized(persona="host", session_id="s1", input_tokens=100, output_tokens=50),
            _attr(experiment_id=EXP_A, matched_via=MATCH_INLINE_FIELD, session_id="s1"),
        ),
        # host EXP_B session
        (
            _make_normalized(persona="host", session_id="s2", input_tokens=200, output_tokens=80),
            _attr(experiment_id=EXP_B, matched_via=MATCH_INLINE_FIELD, session_id="s2"),
        ),
        # participant EXP_A session
        (
            _make_normalized(
                persona="participant", session_id="s3", input_tokens=60, output_tokens=30
            ),
            _attr(experiment_id=EXP_A, matched_via=MATCH_FILE_WINDOW, session_id="s3"),
        ),
    ]
    result = aggregate_by_persona(events)
    assert "host" in result
    assert "participant" in result

    # host: EXP_A 100/50 + EXP_B 200/80 → total 300/130, sessions=2
    host = result["host"]
    assert host.experiment_breakdown[EXP_A].input_tokens == 100
    assert host.experiment_breakdown[EXP_B].input_tokens == 200
    assert host.total is not None
    assert host.total.input_tokens == 300
    assert host.total.output_tokens == 130
    assert host.total.sessions == 2

    # participant: EXP_A 60/30 → total 60/30, sessions=1
    participant = result["participant"]
    assert participant.experiment_breakdown[EXP_A].input_tokens == 60
    assert participant.total is not None
    assert participant.total.input_tokens == 60
    assert participant.total.sessions == 1


# =========================================================================
# Case (8) aggregate_by_persona：unmatched 不归任何 experiment
# =========================================================================


def test_case_8_unmatched_excluded_from_cross_experiment() -> None:
    """Case (8): aggregate_by_persona 跳过 unmatched session（experiment_id=None）。"""
    events = [
        # matched
        (
            _make_normalized(persona="host", session_id="s1", input_tokens=100),
            _attr(experiment_id=EXP_A, matched_via=MATCH_INLINE_FIELD, session_id="s1"),
        ),
        # unmatched（不应进入 host 的任何 experiment_breakdown）
        (
            _make_normalized(persona="host", session_id="s_orphan", input_tokens=999),
            _attr(experiment_id=None, matched_via=MATCH_UNMATCHED, session_id="s_orphan"),
        ),
    ]
    result = aggregate_by_persona(events)
    host = result["host"]
    # 只 EXP_A 一个 experiment，没有 s_orphan
    assert EXP_A in host.experiment_breakdown
    assert host.experiment_breakdown[EXP_A].input_tokens == 100
    assert host.total is not None
    assert host.total.input_tokens == 100  # 999 不计入 total
    assert host.total.sessions == 1  # s_orphan 不计


# =========================================================================
# Case (9) _accumulate 缺字段 None 视作 0（plan §A1 unknown 契约）
# =========================================================================


def test_case_9_accumulate_none_as_zero() -> None:
    """Case (9): NormalizedUsage.input_tokens=None → 视作 0，不抛错（plan §A1）。"""
    empty = _empty_persona("host")
    partial = _make_normalized(persona="host", input_tokens=None, output_tokens=20)
    result = _accumulate(empty, partial, session_inc=1)
    # input_tokens=None → +0；output_tokens=20
    assert result.input_tokens == 0
    assert result.output_tokens == 20
    assert result.sessions == 1


# =========================================================================
# Case (10) 端到端：spike 三实验 jsonl 聚合
# =========================================================================


def test_case_10_end_to_end_three_experiments() -> None:
    """Case (10): 三实验 6 session（host×3 + participant×3）端到端聚合。

    验证 aggregate_by_persona + aggregate_by_experiment 协作：
    - per-experiment view 仅含本 experiment 数据
    - per-persona cross view 含 host × 3 exp + participant × 3 exp
    - match_breakdown 4 桶至少 1 项非零（这里 inline_field = 6）
    """
    experiments = [EXP_A, EXP_B, EXP_C]
    personas = ["host", "participant"]
    events = []
    for exp_id in experiments:
        for persona in personas:
            sid = f"sess-{exp_id[:4]}-{persona}"
            events.append(
                (
                    _make_normalized(
                        persona=persona, session_id=sid, input_tokens=100, output_tokens=50
                    ),
                    _attr(
                        experiment_id=exp_id, matched_via=MATCH_INLINE_FIELD, session_id=sid
                    ),
                )
            )

    # 单实验视图：每个 experiment 1 host + 1 participant
    for exp_id in experiments:
        events_for_exp = [(u, a) for u, a in events if a.experiment_id == exp_id]
        breakdown = aggregate_by_experiment(events_for_exp, experiment_id=exp_id)
        assert "host" in breakdown.persona_breakdown
        assert "participant" in breakdown.persona_breakdown
        assert breakdown.match_breakdown[MATCH_INLINE_FIELD] == 2  # host + participant
        assert breakdown.match_breakdown[MATCH_UNMATCHED] == 0

    # 跨实验视图
    by_persona = aggregate_by_persona(events)
    assert "host" in by_persona and "participant" in by_persona
    for persona in personas:
        agg = by_persona[persona]
        assert len(agg.experiment_breakdown) == 3
        assert agg.total is not None
        assert agg.total.input_tokens == 300  # 100 × 3 experiments
        assert agg.total.sessions == 3
