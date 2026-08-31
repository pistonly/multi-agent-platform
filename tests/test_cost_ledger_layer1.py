"""T5-B Layer 1 collector tests (实验 e63ec33e I1 + A8 case)。

覆盖：
- (a) 基本 assistant 行 → RawUsageEvent 完整字段
- (b) 非 assistant 行（user / queue-operation）→ 跳过
- (c) 损坏 JSONL 行 → RuntimeWarning + 跳过（聚合不崩）
- (d) 缺 sessionId 行 → 仍 emit（attribution 阶段处理空 sessionId）
- (e) 多 persona runtime home 路径 → events 含正确 persona 前缀
- (f) scan_session_jsonl 单文件 vs scan_runtime_homes 三 persona 路径
- (g) 空 jsonl 文件 → 空 iter（不抛错）

mock via tmp_path 写入合成 jsonl；不依赖真实 runtime home（避免 test 跨主机不稳定）。
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cli.cost_ledger.layer1_collector import (  # noqa: E402
    PROJECT_DIR_SUFFIX,
    RUNTIME_HOME_PERSONAS,
    RawUsageEvent,
    _runtime_home_for,
    scan_runtime_homes,
    scan_session_jsonl,
)


def _write_runtime_home(project_root: Path, persona: str, lines: list[str]) -> Path:
    """Helper: write a synthetic jsonl file under runtime home for persona."""
    runtime_home = _runtime_home_for(project_root, persona)
    project_dir = runtime_home / ".claude" / "projects" / PROJECT_DIR_SUFFIX
    project_dir.mkdir(parents=True, exist_ok=True)
    session_file = project_dir / "test-session.jsonl"
    session_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return session_file


def _assistant_line(
    session_id: str = "sess-001",
    ts: str = "2026-08-31T10:00:00Z",
    version: str = "2.1.191",
    usage: dict | None = None,
    type_: str = "assistant",
) -> str:
    return json.dumps(
        {
            "type": type_,
            "sessionId": session_id,
            "timestamp": ts,
            "version": version,
            "message": {
                "id": "chatcmpl-x",
                "model": "claude-sonnet-4-6",
                "usage": usage
                or {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        }
    )


# =========================================================================
# Case (a) 基本 assistant 行 → RawUsageEvent 完整字段
# =========================================================================


def test_case_a_basic_assistant_line_yields_event(tmp_path: Path) -> None:
    """Case (a): 标准 assistant 行 → RawUsageEvent 含 persona/session_id/ts/version。"""
    _write_runtime_home(
        tmp_path,
        "host",
        [_assistant_line(session_id="sess-a1", ts="2026-08-31T10:00:00Z")],
    )
    events = list(scan_runtime_homes(tmp_path))
    assert len(events) == 1
    e = events[0]
    assert isinstance(e, RawUsageEvent)
    assert e.persona == "host"
    assert e.session_id == "sess-a1"
    assert e.ts == "2026-08-31T10:00:00Z"
    assert e.version == "2.1.191"
    assert e.raw_payload["message"]["usage"]["input_tokens"] == 100


# =========================================================================
# Case (b) 非 assistant 行 → 跳过
# =========================================================================


def test_case_b_non_assistant_lines_skipped(tmp_path: Path) -> None:
    """Case (b): type=user / queue-operation 等非 assistant 行 → 跳过。"""
    _write_runtime_home(
        tmp_path,
        "host",
        [
            _assistant_line(type_="user"),
            _assistant_line(type_="queue-operation"),
            json.dumps({"type": "user", "sessionId": "s", "message": {"content": "hi"}}),
            _assistant_line(type_="assistant"),  # 仅这行应被 yield
        ],
    )
    events = list(scan_runtime_homes(tmp_path))
    assert len(events) == 1
    assert events[0].raw_payload["type"] == "assistant"


# =========================================================================
# Case (c) 损坏 JSONL 行 → RuntimeWarning + 跳过
# =========================================================================


def test_case_c_corrupt_line_warns_and_skips(tmp_path: Path) -> None:
    """Case (c): 半截 JSON 行 → RuntimeWarning + 跳过（聚合不崩）。"""
    runtime_home = _runtime_home_for(tmp_path, "host")
    project_dir = runtime_home / ".claude" / "projects" / PROJECT_DIR_SUFFIX
    project_dir.mkdir(parents=True)
    session_file = project_dir / "sess.jsonl"
    session_file.write_text(
        "\n".join(
            [
                '{"type": "assistant", "sessionId": "good", "timestamp": "t1"}',
                "{not valid json",
                "",
                _assistant_line(session_id="sess-after"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        events = list(scan_runtime_homes(tmp_path))
    assert len(events) == 2  # 1 正常 + 1 损坏后的正常
    assert events[0].session_id == "good"
    assert events[1].session_id == "sess-after"
    # 损坏行触发 RuntimeWarning
    assert any(
        issubclass(w.category, RuntimeWarning) and "corrupt JSONL" in str(w.message)
        for w in caught
    )


# =========================================================================
# Case (d) 缺 sessionId 行 → 仍 emit（空字符串 sessionId）
# =========================================================================


def test_case_d_missing_session_id_still_emits(tmp_path: Path) -> None:
    """Case (d): sessionId 缺失 → RawUsageEvent.session_id=""（attribution 阶段处理）。"""
    payload = {
        "type": "assistant",
        # no sessionId
        "timestamp": "2026-08-31T10:00:00Z",
        "version": "2.1.191",
        "message": {"usage": {"input_tokens": 1, "output_tokens": 1}},
    }
    _write_runtime_home(tmp_path, "host", [json.dumps(payload)])
    events = list(scan_runtime_homes(tmp_path))
    assert len(events) == 1
    assert events[0].session_id == ""


# =========================================================================
# Case (e) 多 persona runtime home 路径 → events persona 前缀正确
# =========================================================================


def test_case_e_multiple_personas_yield_distinct_prefixes(tmp_path: Path) -> None:
    """Case (e): host / participant / reviewer 三 persona 都写 → events persona 各自归位。"""
    for persona in RUNTIME_HOME_PERSONAS:
        _write_runtime_home(
            tmp_path,
            persona,
            [_assistant_line(session_id=f"sess-{persona}")],
        )
    events = list(scan_runtime_homes(tmp_path))
    personas_seen = {e.persona for e in events}
    assert personas_seen == {"host", "participant", "reviewer"}
    # session_id 与 persona 一一对应
    for e in events:
        assert e.session_id == f"sess-{e.persona}"


# =========================================================================
# Case (f) scan_session_jsonl 单文件 vs scan_runtime_homes 三 persona 路径
# =========================================================================


def test_case_f_scan_session_jsonl_single_file(tmp_path: Path) -> None:
    """Case (f): scan_session_jsonl 仅扫一个 jsonl 文件，不跨 persona。"""
    file_path = _write_runtime_home(
        tmp_path,
        "host",
        [_assistant_line(session_id="sess-single")],
    )
    events = list(scan_session_jsonl(file_path, persona="host"))
    assert len(events) == 1
    assert events[0].session_id == "sess-single"
    assert events[0].persona == "host"


# =========================================================================
# Case (g) 空 jsonl 文件 → 空 iter（不抛错）
# =========================================================================


def test_case_g_empty_jsonl_returns_empty(tmp_path: Path) -> None:
    """Case (g): 0 字节 jsonl → 空 iter（不抛错；waker 刚启动未写入时常见）。"""
    _write_runtime_home(tmp_path, "host", [])
    events = list(scan_runtime_homes(tmp_path))
    assert events == []


# =========================================================================
# Case (h) 不存在的 runtime home → 空 iter（不抛错）
# =========================================================================


def test_case_h_missing_runtime_home_returns_empty(tmp_path: Path) -> None:
    """Case (h): .map/claude-runtime-home-* 不存在 → 空 iter（跨主机 / 全新 repo）。"""
    events = list(scan_runtime_homes(tmp_path))
    assert events == []


# =========================================================================
# 集成：与真实 spike 数据兼容（仅断言结构，不校验精确数字）
# =========================================================================


def test_integration_real_runtime_home_smoke() -> None:
    """集成 smoke：真实 runtime home（spike_note.md 考证）扫到至少 1 个 event。"""
    project_root = Path(".").resolve()
    # 不强制依赖真实数据；只确保 scan_runtime_homes 调用不抛错
    events = list(scan_runtime_homes(project_root))
    # 真实环境扫描出若干 events；CI / 新机器可能 0 event → 都不抛错
    assert isinstance(events, list)
    if events:
        e = events[0]
        # 真实 spike 数据 event 的字段应齐全
        assert e.persona in RUNTIME_HOME_PERSONAS
        assert e.version  # 不空
        assert e.ts  # 不空
