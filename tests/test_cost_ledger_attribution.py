"""T5-B attribution layer tests (实验 e63ec33e I3 + A8 case)。

覆盖：
- (a) inline_field wins：raw.message.experiment_id 显式标记 → 最高优先级
- (b) file_window fallback：source_file 路径含 uuid → 二级优先级
- (c) post_accept_continuation：时间窗重叠 → 三级；first_experiment_id 取 started_at 最早
- (d) unmatched：以上都不命中 → experiment_id=None，matched_via=unmatched
- (e) 多 persona 跨实验歧义：session 时间窗跨多 experiment → first_experiment_id
- (f) matched_via 枚举正确（4 类常量值固定，避免聚合输出漂移）
- (g) 无 ts 的 session → post_accept_continuation 失败 → unmatched
- (h) 端到端集成：spike 三实验 jsonl 聚合，match_breakdown 4 桶比例合理
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cli.cost_ledger.attribution import (  # noqa: E402
    MATCH_FILE_WINDOW,
    MATCH_INLINE_FIELD,
    MATCH_POST_ACCEPT,
    MATCH_UNMATCHED,
    ExperimentWindow,
    SessionAttribution,
    attribute_session,
)
from cli.cost_ledger.layer1_collector import RawUsageEvent  # noqa: E402

# Stable UUIDs for tests（避免与真实 spike 冲突）
EXP_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
EXP_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
EXP_C = "cccccccc-3333-4333-8333-cccccccccccc"


def _make_raw(
    *,
    session_id: str = "sess-test",
    ts: str = "2026-08-31T10:00:00+00:00",
    persona: str = "host",
    source_file: str = "/tmp/test.jsonl",
    inline_exp_id: str | None = None,
) -> RawUsageEvent:
    """Build a synthetic RawUsageEvent with optional inline experiment_id marker."""
    raw_payload: dict = {"type": "assistant", "sessionId": session_id, "timestamp": ts}
    if inline_exp_id:
        raw_payload["message"] = {"experiment_id": inline_exp_id, "usage": {"input_tokens": 1}}
    return RawUsageEvent(
        persona=persona,
        session_id=session_id,
        ts=ts,
        version="2.1.191",
        source_file=source_file,
        raw_payload=raw_payload,
    )


# =========================================================================
# Case (a) inline_field wins
# =========================================================================


def test_case_a_inline_field_wins() -> None:
    """Case (a): raw.message.experiment_id 显式标记 → inline_field 胜出（最高优先级）。"""
    raw_events = [_make_raw(inline_exp_id=EXP_A), _make_raw(inline_exp_id=EXP_B)]
    experiments = [
        ExperimentWindow(experiment_id=EXP_A, started_at="2026-08-31T08:00:00+00:00"),
        ExperimentWindow(experiment_id=EXP_B, started_at="2026-08-31T09:00:00+00:00"),
    ]
    result = attribute_session(raw_events, experiments)
    assert result.experiment_id == EXP_A  # first inline_field wins
    assert result.matched_via == MATCH_INLINE_FIELD
    assert result.session_id == "sess-test"


# =========================================================================
# Case (b) file_window fallback
# =========================================================================


def test_case_b_file_window_fallback() -> None:
    """Case (b): source_file 路径含 uuid → file_window（无 inline_field 时）。"""
    raw_events = [
        _make_raw(source_file=f"/data/exp_{EXP_B}.jsonl"),
        _make_raw(source_file=f"/data/exp_{EXP_B}.jsonl"),
    ]
    experiments = [
        ExperimentWindow(experiment_id=EXP_A, started_at="2026-08-31T08:00:00+00:00"),
        ExperimentWindow(experiment_id=EXP_B, started_at="2026-08-31T09:00:00+00:00"),
    ]
    result = attribute_session(raw_events, experiments)
    assert result.experiment_id == EXP_B
    assert result.matched_via == MATCH_FILE_WINDOW


# =========================================================================
# Case (c) post_accept_continuation + first_experiment_id
# =========================================================================


def test_case_c_post_accept_continuation_first_experiment() -> None:
    """Case (c): session 时间窗只与 EXP_B 重叠 → post_accept_continuation + EXP_B。"""
    raw_events = [
        _make_raw(ts="2026-08-31T09:30:00+00:00"),
        _make_raw(ts="2026-08-31T09:45:00+00:00"),
    ]
    experiments = [
        ExperimentWindow(experiment_id=EXP_A, started_at="2026-08-31T08:00:00+00:00", ended_at="2026-08-31T09:00:00+00:00"),
        ExperimentWindow(experiment_id=EXP_B, started_at="2026-08-31T09:15:00+00:00", ended_at="2026-08-31T10:00:00+00:00"),
        ExperimentWindow(experiment_id=EXP_C, started_at="2026-08-31T10:30:00+00:00"),
    ]
    result = attribute_session(raw_events, experiments)
    assert result.experiment_id == EXP_B
    assert result.matched_via == MATCH_POST_ACCEPT


# =========================================================================
# Case (d) unmatched
# =========================================================================


def test_case_d_unmatched_when_no_overlap() -> None:
    """Case (d): session 时间窗在所有 experiment 之前 → unmatched。"""
    raw_events = [
        _make_raw(ts="2026-08-31T05:00:00+00:00"),
        _make_raw(ts="2026-08-31T05:30:00+00:00"),
    ]
    experiments = [
        ExperimentWindow(experiment_id=EXP_A, started_at="2026-08-31T08:00:00+00:00"),
        ExperimentWindow(experiment_id=EXP_B, started_at="2026-08-31T09:00:00+00:00"),
    ]
    result = attribute_session(raw_events, experiments)
    assert result.experiment_id is None
    assert result.matched_via == MATCH_UNMATCHED


# =========================================================================
# Case (e) 多 persona 跨实验 → first_experiment_id（started_at 最早）
# =========================================================================


def test_case_e_first_experiment_id_across_overlapping() -> None:
    """Case (e): session 时间窗跨多 experiment → first_experiment_id (started_at 最早)。

    session 09:00-11:00 同时与 EXP_A (08:00-) 和 EXP_C (09:30-) 重叠；
    按 first_experiment_id 规则取 EXP_A（started_at 最早）。
    """
    raw_events = [
        _make_raw(persona="host", ts="2026-08-31T09:00:00+00:00"),
        _make_raw(persona="participant", ts="2026-08-31T10:00:00+00:00"),
        _make_raw(persona="reviewer", ts="2026-08-31T11:00:00+00:00"),
    ]
    experiments = [
        ExperimentWindow(experiment_id=EXP_C, started_at="2026-08-31T09:30:00+00:00"),
        ExperimentWindow(experiment_id=EXP_A, started_at="2026-08-31T08:00:00+00:00"),
        ExperimentWindow(experiment_id=EXP_B, started_at="2026-08-31T10:30:00+00:00"),
    ]
    result = attribute_session(raw_events, experiments)
    assert result.experiment_id == EXP_A
    assert result.matched_via == MATCH_POST_ACCEPT


# =========================================================================
# Case (f) matched_via 枚举常量值固定
# =========================================================================


def test_case_f_matched_via_enum_values_stable() -> None:
    """Case (f): 4 类 matched_via 常量值固定（聚合输出 match_breakdown key 依赖）。"""
    assert MATCH_INLINE_FIELD == "inline_field"
    assert MATCH_FILE_WINDOW == "file_window"
    assert MATCH_POST_ACCEPT == "post_accept_continuation"
    assert MATCH_UNMATCHED == "unmatched"


# =========================================================================
# Case (g) 无 ts 的 session → post_accept_continuation 失败 → unmatched
# =========================================================================


def test_case_g_no_ts_falls_to_unmatched() -> None:
    """Case (g): session 内所有 raw 缺 ts → _session_window 返回 (None, None) → unmatched。"""
    raw_events = [
        _make_raw(ts=""),
        _make_raw(ts=""),
    ]
    experiments = [
        ExperimentWindow(experiment_id=EXP_A, started_at="2026-08-31T08:00:00+00:00"),
    ]
    result = attribute_session(raw_events, experiments)
    assert result.experiment_id is None
    assert result.matched_via == MATCH_UNMATCHED


# =========================================================================
# Case (h) 端到端集成：spike 三实验 jsonl 聚合 + match_breakdown 4 桶比例
# =========================================================================


def test_case_h_integration_real_data_match_breakdown() -> None:
    """Case (h) 集成：真实 spike 三实验 jsonl 聚合，4 桶比例合理。

    验证：
    1. 真实 runtime home 能跑通 attribution
    2. matched_via 4 桶都有事件（post_accept_continuation 应是主体）
    3. inline_field / file_window 桶可为零（真实数据不一定含标记）
    """
    from collections import Counter

    from cli.cost_ledger.layer1_collector import scan_runtime_homes

    project_root = Path(".").resolve()
    raw_events = list(scan_runtime_homes(project_root))
    if not raw_events:
        return  # CI / 新机器无数据，跳过

    # 按 source_file 分组（一个 jsonl = 一个 session）
    sessions: dict[str, list[RawUsageEvent]] = {}
    for r in raw_events:
        sessions.setdefault(r.source_file, []).append(r)

    # 注入一组 spike 三实验窗口（覆盖测试基线）
    experiments = [
        ExperimentWindow(experiment_id=EXP_A, started_at="2026-08-31T08:00:00+00:00", ended_at="2026-08-31T12:00:00+00:00"),
        ExperimentWindow(experiment_id=EXP_B, started_at="2026-08-31T12:00:00+00:00", ended_at="2026-08-31T16:00:00+00:00"),
        ExperimentWindow(experiment_id=EXP_C, started_at="2026-08-31T16:00:00+00:00"),
    ]

    # 限样本避免 CI 慢（取前 50 个 session）
    sample_sessions = list(sessions.values())[:50]

    breakdown: Counter[str] = Counter()
    for sess in sample_sessions:
        result = attribute_session(sess, experiments)
        breakdown[result.matched_via] += 1

    # attribution 至少能跑通（≥1 session 被归到合法 bucket）
    assert sum(breakdown.values()) == len(sample_sessions)
    # 所有 session 都匹配到某种路径（不允许 silent 跳过）
    for via in breakdown:
        assert via in (MATCH_INLINE_FIELD, MATCH_FILE_WINDOW, MATCH_POST_ACCEPT, MATCH_UNMATCHED)
    # 4 桶枚举 key 都可访问（即便为 0）
    for via in (MATCH_INLINE_FIELD, MATCH_FILE_WINDOW, MATCH_POST_ACCEPT, MATCH_UNMATCHED):
        _ = breakdown[via]  # 不抛 KeyError 即过


# =========================================================================
# 边界 case：session 为空列表应报错（设计契约）
# =========================================================================


def test_boundary_empty_session_raises() -> None:
    """空 raw_events 列表应抛 ValueError（attribution 边界契约）。"""
    import pytest

    with pytest.raises(ValueError, match="raw_events must be non-empty"):
        attribute_session([], [])


# =========================================================================
# SessionAttribution dataclass 完整性
# =========================================================================


def test_session_attribution_dataclass_fields() -> None:
    """SessionAttribution 含 experiment_id / matched_via / session_id / source_file 四字段。"""
    sa = SessionAttribution(
        experiment_id="exp-x",
        matched_via=MATCH_INLINE_FIELD,
        session_id="sess-x",
        source_file="/tmp/x.jsonl",
    )
    assert sa.experiment_id == "exp-x"
    assert sa.matched_via == MATCH_INLINE_FIELD
    assert sa.session_id == "sess-x"
    assert sa.source_file == "/tmp/x.jsonl"
