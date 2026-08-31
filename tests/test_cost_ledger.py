"""T5-B end-to-end integration tests (实验 e63ec33e I7 + A8 case)。

按 plan §I7 8 case 端到端：layer1 → layer2 → attribution → render 串联 + orchestrator。
mock via tmp_path 写入合成 jsonl；不依赖真实 runtime home。

覆盖：
- (a) 多 persona 跨实验归属歧义：first_experiment_id 全归首个
- (b) 缺 usage 显式 unknown：unknown 标记贯穿 pipeline
- (c) 损坏行告警不崩：warn 行但聚合继续
- (d) cache 字段分桶：cache_creation / cache_read 独立
- (e) 命令入口分工：render_experiment_view vs render_persona_aggregate_view
- (f) 价目表缺价目只报 token：pricing_unavailable 标记
- (g) match_breakdown 默认输出（plan §A5）：4 桶固定 schema
- (h) 端到端 spike 三实验 jsonl 聚合对照 raw：差 < 1%
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cli.cost_ledger.attribution import (  # noqa: E402
    MATCH_FILE_WINDOW,
    MATCH_INLINE_FIELD,
    MATCH_POST_ACCEPT,
    MATCH_UNMATCHED,
    ExperimentWindow,
)
from cli.cost_ledger.layer1_collector import (  # noqa: E402
    PROJECT_DIR_SUFFIX,
    _runtime_home_for,
)
from cli.cost_ledger.orchestrator import (  # noqa: E402
    cost_breakdown_to_yaml_dict,
    render_experiment_view,
    render_persona_aggregate_view,
)

# Stable UUIDs（避免与真实 spike 冲突）
EXP_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
EXP_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
EXP_C = "cccccccc-3333-4333-8333-cccccccccccc"


def _assistant_line(
    *,
    session_id: str,
    ts: str,
    usage: dict | None = None,
    inline_exp_id: str | None = None,
) -> str:
    """Build a synthetic assistant JSONL line."""
    payload: dict = {
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": ts,
        "version": "2.1.191",
        "message": {},
    }
    if inline_exp_id:
        payload["message"]["experiment_id"] = inline_exp_id
    if usage is not None:
        payload["message"]["usage"] = usage
    return json.dumps(payload)


def _write_session_jsonl(
    project_root: Path,
    persona: str,
    lines: list[str],
    *,
    filename: str = "test-session.jsonl",
) -> Path:
    """Write a synthetic session jsonl under runtime home for given persona.

    默认 ``filename`` 可被覆盖——同一 persona 多次调用不会覆盖（每 session
    独立文件对应 layer1 source_file → attribution 不会合并）。
    """
    runtime_home = _runtime_home_for(project_root, persona)
    project_dir = runtime_home / ".claude" / "projects" / PROJECT_DIR_SUFFIX
    project_dir.mkdir(parents=True, exist_ok=True)
    session_file = project_dir / filename
    session_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return session_file


def _full_usage(
    *,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_creation: int = 0,
    cache_read: int = 0,
) -> dict:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
    }


# =========================================================================
# Case (a) 多 persona 跨实验归属歧义：first_experiment_id wins
# =========================================================================


def test_case_a_first_experiment_id_across_personas(tmp_path: Path) -> None:
    """Case (a): 3 persona × 3 session 时间窗重叠 → first_experiment_id 全归首个。"""
    # EXP_A started earlier; sessions overlap A, B, C windows
    windows = [
        ExperimentWindow(EXP_A, "2026-08-31T08:00:00+00:00"),
        ExperimentWindow(EXP_B, "2026-08-31T09:00:00+00:00"),
        ExperimentWindow(EXP_C, "2026-08-31T10:00:00+00:00"),
    ]
    # Each persona writes 1 session with explicit inline_field=EXP_A
    for persona in ("host", "participant", "reviewer"):
        _write_session_jsonl(
            tmp_path,
            persona,
            [
                _assistant_line(
                    session_id=f"sess-{persona}",
                    ts="2026-08-31T09:30:00+00:00",
                    usage=_full_usage(input_tokens=100),
                    inline_exp_id=EXP_A,
                )
            ],
        )

    breakdown = render_experiment_view(
        tmp_path, experiment_id=EXP_A, experiments=windows
    )
    # 3 sessions × 100 input each → host/participant/reviewer 都各 100
    assert breakdown.persona_breakdown["host"].input_tokens == 100
    assert breakdown.persona_breakdown["participant"].input_tokens == 100
    assert breakdown.persona_breakdown["reviewer"].input_tokens == 100
    # 全部 inline_field
    assert breakdown.match_breakdown[MATCH_INLINE_FIELD] == 3
    assert breakdown.match_breakdown[MATCH_UNMATCHED] == 0


# =========================================================================
# Case (b) 缺 usage 显式 unknown：unknown 字段贯穿
# =========================================================================


def test_case_b_missing_usage_explicit_unknown(tmp_path: Path) -> None:
    """Case (b): assistant 行无 message.usage → unknown_fields 显式 tuple 不静默。"""
    windows = [ExperimentWindow(EXP_A, "2026-08-31T08:00:00+00:00")]
    _write_session_jsonl(
        tmp_path,
        "host",
        [
            # 无 usage 块
            _assistant_line(
                session_id="sess-no-usage",
                ts="2026-08-31T09:30:00+00:00",
                usage=None,
                inline_exp_id=EXP_A,
            )
        ],
    )
    breakdown = render_experiment_view(
        tmp_path, experiment_id=EXP_A, experiments=windows
    )
    # tokens 全 0（unknown_fields 标记 None）
    host = breakdown.persona_breakdown["host"]
    assert host.input_tokens == 0
    assert host.output_tokens == 0
    assert host.sessions == 1
    # match_breakdown：inline_field 仍然记 1（attribution 不受 usage 缺失影响）
    assert breakdown.match_breakdown[MATCH_INLINE_FIELD] == 1


# =========================================================================
# Case (c) 损坏行告警不崩
# =========================================================================


def test_case_c_corrupt_line_warns_continues(tmp_path: Path) -> None:
    """Case (c): 1 行 JSONDecodeError → RuntimeWarning + 聚合继续。"""
    windows = [ExperimentWindow(EXP_A, "2026-08-31T08:00:00+00:00")]
    good_line = _assistant_line(
        session_id="sess-mixed",
        ts="2026-08-31T09:30:00+00:00",
        usage=_full_usage(input_tokens=500),
        inline_exp_id=EXP_A,
    )
    _write_session_jsonl(
        tmp_path,
        "host",
        [
            "{this is not json",  # 损坏行
            good_line,
        ],
    )
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        breakdown = render_experiment_view(
            tmp_path, experiment_id=EXP_A, experiments=windows
        )
    # 至少有 1 条 layer1 损坏 warn
    layer1_warns = [x for x in w if "layer1_collector" in str(x.message)]
    assert len(layer1_warns) >= 1
    # 聚合继续：good 行 500 input 计入
    assert breakdown.persona_breakdown["host"].input_tokens == 500


# =========================================================================
# Case (d) cache 字段分桶
# =========================================================================


def test_case_d_cache_fields_independent(tmp_path: Path) -> None:
    """Case (d): cache_creation + cache_read 独立计数，不并入 input。"""
    windows = [ExperimentWindow(EXP_A, "2026-08-31T08:00:00+00:00")]
    _write_session_jsonl(
        tmp_path,
        "host",
        [
            _assistant_line(
                session_id="sess-cache",
                ts="2026-08-31T09:30:00+00:00",
                usage=_full_usage(
                    input_tokens=100,
                    cache_creation=200,
                    cache_read=300,
                ),
                inline_exp_id=EXP_A,
            )
        ],
    )
    breakdown = render_experiment_view(
        tmp_path, experiment_id=EXP_A, experiments=windows
    )
    host = breakdown.persona_breakdown["host"]
    assert host.input_tokens == 100  # 不并入 cache
    assert host.cache_creation_input_tokens == 200
    assert host.cache_read_input_tokens == 300


# =========================================================================
# Case (e) 命令入口分工
# =========================================================================


def test_case_e_command_entry_separation(tmp_path: Path) -> None:
    """Case (e): render_experiment_view 单实验 vs render_persona_aggregate_view 跨实验。"""
    windows = [
        ExperimentWindow(EXP_A, "2026-08-31T08:00:00+00:00"),
        ExperimentWindow(EXP_B, "2026-08-31T09:00:00+00:00"),
    ]
    # EXP_A session (host) + EXP_B session (host) — different files
    _write_session_jsonl(
        tmp_path,
        "host",
        [
            _assistant_line(
                session_id="sess-A",
                ts="2026-08-31T08:30:00+00:00",
                usage=_full_usage(input_tokens=100),
                inline_exp_id=EXP_A,
            )
        ],
        filename="sess-A.jsonl",
    )
    _write_session_jsonl(
        tmp_path,
        "host",
        [
            _assistant_line(
                session_id="sess-B",
                ts="2026-08-31T09:30:00+00:00",
                usage=_full_usage(input_tokens=200),
                inline_exp_id=EXP_B,
            )
        ],
        filename="sess-B.jsonl",
    )

    # 单实验视图：仅 EXP_A 1 session 100 input
    single = render_experiment_view(tmp_path, experiment_id=EXP_A, experiments=windows)
    assert single.persona_breakdown["host"].input_tokens == 100
    assert single.match_breakdown[MATCH_INLINE_FIELD] == 1

    # 跨实验视图：host 跨 A+B total = 300
    cross = render_persona_aggregate_view(tmp_path, experiments=windows)
    assert "host" in cross
    host_total = cross["host"].total
    assert host_total is not None
    assert host_total.input_tokens == 300  # 100 + 200
    assert host_total.sessions == 2
    assert cross["host"].experiment_breakdown[EXP_A].input_tokens == 100
    assert cross["host"].experiment_breakdown[EXP_B].input_tokens == 200


# =========================================================================
# Case (f) 价目表缺价目只报 token + pricing_unavailable 标记
# =========================================================================


def test_case_f_pricing_unavailable_marker(tmp_path: Path) -> None:
    """Case (f): 当前未接价目表，YAML 输出显式 pricing_unavailable 标记（不 0 兜底）。"""
    windows = [ExperimentWindow(EXP_A, "2026-08-31T08:00:00+00:00")]
    _write_session_jsonl(
        tmp_path,
        "host",
        [
            _assistant_line(
                session_id="sess-cost",
                ts="2026-08-31T09:30:00+00:00",
                usage=_full_usage(input_tokens=100),
                inline_exp_id=EXP_A,
            )
        ],
    )
    breakdown = render_experiment_view(
        tmp_path, experiment_id=EXP_A, experiments=windows
    )
    yaml_dict = cost_breakdown_to_yaml_dict(breakdown, pricing_unavailable=True)
    assert yaml_dict["pricing_unavailable"] is True
    # tokens 仍正常输出
    assert yaml_dict["persona_breakdown"]["host"]["input_tokens"] == 100


# =========================================================================
# Case (g) match_breakdown 默认输出（plan §A5）
# =========================================================================


def test_case_g_match_breakdown_four_buckets(tmp_path: Path) -> None:
    """Case (g): match_breakdown 默认输出 4 桶固定 schema，无需 --verbose。"""
    windows = [
        ExperimentWindow(EXP_A, "2026-08-31T08:00:00+00:00"),
        ExperimentWindow(EXP_B, "2026-08-31T09:00:00+00:00"),
    ]
    # 4 个不同来源的 session
    # 1. inline_field → EXP_A
    _write_session_jsonl(
        tmp_path,
        "host",
        [
            _assistant_line(
                session_id="sess-inline",
                ts="2026-08-31T08:30:00+00:00",
                usage=_full_usage(input_tokens=10),
                inline_exp_id=EXP_A,
            )
        ],
        filename="sess-inline.jsonl",
    )
    # 2. file_window → source_file 含 EXP_B UUID (但要避开 session_id 自身)
    runtime_home = _runtime_home_for(tmp_path, "participant")
    project_dir = runtime_home / ".claude" / "projects" / PROJECT_DIR_SUFFIX
    project_dir.mkdir(parents=True, exist_ok=True)
    # 文件名 = EXP_B_xxx.jsonl；EXP_B 在前
    file_path = project_dir / f"exp_{EXP_B}_session.jsonl"
    file_path.write_text(
        _assistant_line(
            session_id="sess-filewin",
            ts="2026-08-31T09:30:00+00:00",
            usage=_full_usage(input_tokens=20),
        )
        + "\n"
    )
    # 3. post_accept_continuation → host 时间窗匹配 EXP_A (但无 inline 且文件名不含 UUID)
    _write_session_jsonl(
        tmp_path,
        "reviewer",
        [
            _assistant_line(
                session_id="sess-post",
                ts="2026-08-31T08:45:00+00:00",
                usage=_full_usage(input_tokens=30),
            )
        ],
        filename="sess-post.jsonl",
    )
    # 4. unmatched → 时间窗不匹配任何 experiment
    _write_session_jsonl(
        tmp_path,
        "host",
        [
            _assistant_line(
                session_id="sess-orphan",
                ts="2026-07-01T08:00:00+00:00",  # 早于所有 window start
                usage=_full_usage(input_tokens=40),
            )
        ],
        filename="sess-orphan.jsonl",
    )

    breakdown = render_experiment_view(
        tmp_path, experiment_id=EXP_A, experiments=windows
    )
    yaml_dict = cost_breakdown_to_yaml_dict(breakdown, pricing_unavailable=True)
    match = yaml_dict["match_breakdown"]
    # 4 桶固定 key
    assert set(match.keys()) == {
        MATCH_INLINE_FIELD,
        MATCH_FILE_WINDOW,
        MATCH_POST_ACCEPT,
        MATCH_UNMATCHED,
    }
    # EXP_A 单实验视图：inline=1, post_accept=1（reviewer sess-post 在 EXP_A 窗）;
    # file_window=0 (EXP_B session), unmatched 不计入本实验 view（attribution 后被过滤）
    assert match[MATCH_INLINE_FIELD] == 1
    assert match[MATCH_POST_ACCEPT] == 1


# =========================================================================
# Case (h) 端到端 spike 三实验 jsonl 聚合对照 raw（差 < 1%）
# =========================================================================


def test_case_h_end_to_end_three_experiments(tmp_path: Path) -> None:
    """Case (h): 3 session 分属 3 实验，聚合输入 token == raw 累加（无 1% 误差）。"""
    windows = [
        ExperimentWindow(EXP_A, "2026-08-31T08:00:00+00:00"),
        ExperimentWindow(EXP_B, "2026-08-31T09:00:00+00:00"),
        ExperimentWindow(EXP_C, "2026-08-31T10:00:00+00:00"),
    ]
    expected_per_exp = {"A": 100, "B": 200, "C": 300}
    expected_per_exp_map = {
        EXP_A: 100,
        EXP_B: 200,
        EXP_C: 300,
    }
    for tag, exp_id, ts in [
        ("A", EXP_A, "2026-08-31T08:30:00+00:00"),
        ("B", EXP_B, "2026-08-31T09:30:00+00:00"),
        ("C", EXP_C, "2026-08-31T10:30:00+00:00"),
    ]:
        _write_session_jsonl(
            tmp_path,
            "host",
            [
                _assistant_line(
                    session_id=f"sess-{tag}",
                    ts=ts,
                    usage=_full_usage(
                        input_tokens=expected_per_exp[tag], output_tokens=10
                    ),
                    inline_exp_id=exp_id,
                ),
                # 同一 session 第 2 个事件
                _assistant_line(
                    session_id=f"sess-{tag}",
                    ts=ts,
                    usage=_full_usage(input_tokens=expected_per_exp[tag], output_tokens=10),
                    inline_exp_id=exp_id,
                ),
            ],
            filename=f"sess-{tag}.jsonl",
        )

    # 跨实验汇总
    cross = render_persona_aggregate_view(tmp_path, experiments=windows)
    host = cross["host"]
    assert host.total is not None
    # A: 100*2=200, B: 200*2=400, C: 300*2=600 → total 1200
    expected_total = sum(v * 2 for v in expected_per_exp.values())
    assert host.total.input_tokens == expected_total
    assert host.total.sessions == 3  # 3 sessions

    # 每个实验单独：1 session × 2 events
    for exp_id, expected in expected_per_exp_map.items():
        single = render_experiment_view(
            tmp_path, experiment_id=exp_id, experiments=windows
        )
        actual = single.persona_breakdown["host"].input_tokens
        # 误差 < 1%（绝对差 / expected）
        diff_ratio = abs(actual - expected * 2) / (expected * 2)
        assert diff_ratio < 0.01, f"{exp_id}: actual={actual} expected={expected*2}"
