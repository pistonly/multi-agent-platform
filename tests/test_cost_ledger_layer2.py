"""T5-B Layer 2 mapper tests (实验 e63ec33e I2 + A8 case)。

覆盖：
- (a) 已知 version（2.1.191）+ 完整 usage 字段 → NormalizedUsage 4 字段齐全 + unknown=()
- (b) 缺字段 → NormalizedUsage 对应字段 None + unknown_fields 显式 tuple
  （A1 契约：不静默丢弃）
- (c) cache 字段分桶：cache_creation_input_tokens + cache_read_input_tokens
  独立计数，不并入 input（A6 契约）
- (d) version 未在 VERSION_FIELD_MAP → 4 字段全 unknown（不兜底 0，A6 边界）
- (e) legacy version schema（"prompt_tokens" 别名）→ 正确归一化
- (f) usage 字段类型错误（如 bool / string）→ None + unknown（A1 契约）
- (g) 缺 message.usage 块 → 4 字段全 None + 4 unknown
- (h) map_events 流式入口：iterator → iterator，1:1 映射
- (i) is_complete property：所有字段齐全 → True；任一缺 → False
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cli.cost_ledger.layer1_collector import RawUsageEvent  # noqa: E402
from cli.cost_ledger.layer2_mapper import (  # noqa: E402
    FIELD_INPUT,
    FIELD_OUTPUT,
    map_event,
    map_events,
)


def _make_event(
    *,
    version: str = "2.1.191",
    usage: dict | None = None,
    session_id: str = "sess-test",
    ts: str = "2026-08-31T10:00:00Z",
    persona: str = "host",
    source_file: str = "/tmp/test.jsonl",
) -> RawUsageEvent:
    """Build a synthetic ``RawUsageEvent`` with optional usage block."""
    raw_payload: dict = {"type": "assistant", "sessionId": session_id, "timestamp": ts, "version": version}
    if usage is not None:
        raw_payload["message"] = {"usage": usage}
    return RawUsageEvent(
        persona=persona,
        session_id=session_id,
        ts=ts,
        version=version,
        source_file=source_file,
        raw_payload=raw_payload,
    )


# =========================================================================
# Case (a) 已知 version + 完整 usage → NormalizedUsage 齐全 + unknown=()
# =========================================================================


def test_case_a_known_version_complete_usage() -> None:
    """Case (a): 2.1.191 + 完整 4 字段 → NormalizedUsage 字段齐全 + unknown=()。"""
    ev = _make_event(
        usage={
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 20,
        }
    )
    n = map_event(ev)
    assert n.input_tokens == 100
    assert n.output_tokens == 50
    assert n.cache_creation_input_tokens == 10
    assert n.cache_read_input_tokens == 20
    assert n.unknown_fields == ()
    assert n.is_complete is True


# =========================================================================
# Case (b) 缺字段 → 对应字段 None + unknown_fields 显式 tuple
# =========================================================================


def test_case_b_missing_field_in_unknown_tuple() -> None:
    """Case (b): 缺 output_tokens → unknown_fields 含 (FIELD_OUTPUT, 'output_tokens')。"""
    ev = _make_event(
        usage={
            "input_tokens": 100,
            # output_tokens 缺
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 20,
        }
    )
    n = map_event(ev)
    assert n.input_tokens == 100
    assert n.output_tokens is None
    assert n.cache_creation_input_tokens == 10
    assert n.cache_read_input_tokens == 20
    # unknown tuple 含 FIELD_OUTPUT
    unknown_targets = {t for t, _ in n.unknown_fields}
    assert FIELD_OUTPUT in unknown_targets
    assert len(n.unknown_fields) == 1
    assert n.is_complete is False


# =========================================================================
# Case (c) cache 字段分桶独立计数
# =========================================================================


def test_case_c_cache_fields_counted_independently() -> None:
    """Case (c): cache_creation + cache_read 独立；不并入 input。"""
    ev = _make_event(
        usage={
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 1000,
            "cache_read_input_tokens": 2000,
        }
    )
    n = map_event(ev)
    assert n.input_tokens == 100  # 不含 cache
    assert n.cache_creation_input_tokens == 1000
    assert n.cache_read_input_tokens == 2000


# =========================================================================
# Case (d) version 未在 VERSION_FIELD_MAP → 4 字段全 unknown
# =========================================================================


def test_case_d_unknown_version_full_unknown_no_zero_fallback() -> None:
    """Case (d): version 不在映射表 → 4 字段全 None + 4 unknown（不 0 兜底）。"""
    ev = _make_event(
        version="99.99.99-future",
        usage={
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 20,
        },
    )
    n = map_event(ev)
    assert n.input_tokens is None
    assert n.output_tokens is None
    assert n.cache_creation_input_tokens is None
    assert n.cache_read_input_tokens is None
    assert len(n.unknown_fields) == 4
    assert n.is_complete is False


# =========================================================================
# Case (e) legacy version schema → 正确归一化
# =========================================================================


def test_case_e_legacy_version_prompt_tokens_alias() -> None:
    """Case (e): legacy schema (prompt_tokens / completion_tokens) → 正确归一化。"""
    ev = _make_event(
        version="legacy",
        usage={
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 20,
        },
    )
    n = map_event(ev)
    assert n.input_tokens == 100  # prompt_tokens → input_tokens
    assert n.output_tokens == 50
    assert n.cache_creation_input_tokens == 10
    assert n.cache_read_input_tokens == 20
    assert n.is_complete is True


# =========================================================================
# Case (f) usage 字段类型错误 → None + unknown
# =========================================================================


def test_case_f_usage_field_wrong_type() -> None:
    """Case (f): usage.input_tokens 是 string / bool → None + unknown。"""
    ev = _make_event(
        usage={
            "input_tokens": "not-an-int",  # 字符串
            "output_tokens": True,  # bool
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 20,
        }
    )
    n = map_event(ev)
    assert n.input_tokens is None
    assert n.output_tokens is None
    assert n.cache_creation_input_tokens == 10
    assert n.cache_read_input_tokens == 20
    unknown_targets = {t for t, _ in n.unknown_fields}
    assert FIELD_INPUT in unknown_targets
    assert FIELD_OUTPUT in unknown_targets


# =========================================================================
# Case (g) 缺 message.usage 块 → 4 字段全 None + 4 unknown
# =========================================================================


def test_case_g_missing_usage_block() -> None:
    """Case (g): message.usage 完全缺失 → 4 字段全 None + 4 unknown。"""
    ev = _make_event(usage=None)
    n = map_event(ev)
    assert n.input_tokens is None
    assert n.output_tokens is None
    assert n.cache_creation_input_tokens is None
    assert n.cache_read_input_tokens is None
    assert len(n.unknown_fields) == 4


# =========================================================================
# Case (h) map_events 流式入口
# =========================================================================


def test_case_h_map_events_streams_one_to_one() -> None:
    """Case (h): map_events(iterator) → 1:1 映射；空输入 → 空输出。"""
    events = [
        _make_event(usage={"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}),
        _make_event(usage={"input_tokens": 2, "output_tokens": 2, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}),
        _make_event(usage={"input_tokens": 3, "output_tokens": 3, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}),
    ]
    out = list(map_events(iter(events)))
    assert len(out) == 3
    assert [n.input_tokens for n in out] == [1, 2, 3]

    # 空输入
    assert list(map_events(iter([]))) == []


# =========================================================================
# Case (i) is_complete property
# =========================================================================


def test_case_i_is_complete_property() -> None:
    """Case (i): is_complete 仅在 4 字段全有值时为 True。"""
    full = _make_event(
        usage={
            "input_tokens": 1,
            "output_tokens": 1,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
    )
    assert map_event(full).is_complete is True

    # 缺一个 → False
    partial = _make_event(
        usage={
            "input_tokens": 1,
            "output_tokens": 1,
            "cache_creation_input_tokens": 0,
            # cache_read 缺
        }
    )
    assert map_event(partial).is_complete is False

    # 全 None → False
    empty = _make_event(usage=None)
    assert map_event(empty).is_complete is False


# =========================================================================
# 集成 smoke：真实数据扫到的 events 全 map 后 unknown=0（2.1.191 schema）
# =========================================================================


def test_integration_real_data_maps_clean() -> None:
    """集成 smoke：真实 runtime home 数据通过 layer2 映射后 unknown 应为 0。"""
    from cli.cost_ledger.layer1_collector import scan_runtime_homes

    project_root = Path(".").resolve()
    raw_events = list(scan_runtime_homes(project_root))
    if not raw_events:
        return  # CI / 新机器无数据，跳过
    # 限制样本数（避免 41k events 慢）
    sample = raw_events[:100]
    norms = list(map_events(iter(sample)))
    assert len(norms) == len(sample)
    # 真实数据 schema 2.1.191 → unknown 应为 0
    incomplete = [n for n in norms if n.unknown_fields]
    assert incomplete == [], (
        f"真实 2.1.191 数据应完整映射；found {len(incomplete)} incomplete events"
    )
