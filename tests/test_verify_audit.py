"""T7-a verify-audit 漂移检测回归测试 (实验 e6d23886 I9)。

10 case (a)-(j) 与 plan §A1-A10 验收对齐 + plan §验收 (a)-(j)：
- (a) D001 close_no_audit: status=closed + audit 存在 + 无 close 行
- (b) D002 phase_no_transition: exp phase=running/done/approved + 无 phase_transition
- (c) D003 round_mismatch: fm round 与 audit 最后 round_advanced 不一致
- (d) D004 close_reason_invalid: close_reason 不在合法枚举
- (e) D005 archive_waive_no_audit: archived/waive_reason 无对应 audit 凭据
- (f) 双格式输出 json + human 内容覆盖一致（核心字段 drift_id/kind/path）
- (g) 退出码: 0 clean / 1 drift / 2 corrupted
- (h) scanner CLOSE_REASON_LEGAL 与 sdk map_fs.validation CLOSE_REASON_LEGAL 单源同步
- (i) drift_id 累积编号: D001_001, D001_002 同类递增
- (j) workspace 无 map/ 目录 → 0 漂移（不报错，drift_detector.count=0）
"""
from __future__ import annotations

import json
from pathlib import Path

from map_fs import validation as fs_validation

from cli.verify_audit import output as verify_audit_output
from cli.verify_audit.scanner import (
    CLOSE_REASON_LEGAL,
    DriftDetector,
    scan_plane_audit,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_topic(
    workspace: Path,
    slug: str,
    *,
    fm: dict[str, str],
    audit_lines: list[str] | None = None,
) -> Path:
    """写 map/topics/<slug>/index.md + 可选 audit.jsonl。"""
    topic_dir = workspace / "map" / "topics" / slug
    topic_dir.mkdir(parents=True, exist_ok=True)
    fm_text = "\n".join(f"{k}: {v}" for k, v in fm.items())
    (topic_dir / "index.md").write_text(
        f"---\n{fm_text}\n---\n", encoding="utf-8"
    )
    if audit_lines is not None:
        (topic_dir / "audit.jsonl").write_text(
            "\n".join(audit_lines) + "\n", encoding="utf-8"
        )
    return topic_dir


def _write_experiment(
    workspace: Path,
    slug: str,
    *,
    fm: dict[str, str],
    audit_lines: list[str] | None = None,
) -> Path:
    """写 map/experiments/<slug>/index.md + 可选 audit.jsonl。"""
    exp_dir = workspace / "map" / "experiments" / slug
    exp_dir.mkdir(parents=True, exist_ok=True)
    fm_text = "\n".join(f"{k}: {v}" for k, v in fm.items())
    (exp_dir / "index.md").write_text(
        f"---\n{fm_text}\n---\n", encoding="utf-8"
    )
    if audit_lines is not None:
        (exp_dir / "audit.jsonl").write_text(
            "\n".join(audit_lines) + "\n", encoding="utf-8"
        )
    return exp_dir


# ---------------------------------------------------------------------------
# (a) D001 close_no_audit
# ---------------------------------------------------------------------------


def test_a_d001_close_no_audit(tmp_path: Path) -> None:
    """topic status=closed + audit.jsonl 存在 + 无 close action 行 → D001 检出。"""
    _write_topic(
        tmp_path,
        "t-closed-no-audit",
        fm={"status": "closed", "close_reason": "experiment_done"},
        audit_lines=[json.dumps({"action": "round_advanced", "fields": {"round_number": 1}})],
    )

    detector = scan_plane_audit(tmp_path)
    kinds = [d.kind for d in detector.drifts]
    assert "close_no_audit" in kinds
    close_drift = next(d for d in detector.drifts if d.kind == "close_no_audit")
    assert close_drift.field == "status"
    assert close_drift.current_value == "closed"


# ---------------------------------------------------------------------------
# (b) D002 phase_no_transition
# ---------------------------------------------------------------------------


def test_b_d002_phase_no_transition(tmp_path: Path) -> None:
    """experiment phase=running + audit 存在 + 无 phase_transition → D002 检出。"""
    _write_experiment(
        tmp_path,
        "e-running-no-trans",
        fm={"phase": "running"},
        audit_lines=[json.dumps({"action": "create"})],
    )

    detector = scan_plane_audit(tmp_path)
    kinds = [d.kind for d in detector.drifts]
    assert "phase_no_transition" in kinds


# ---------------------------------------------------------------------------
# (c) D003 round_mismatch
# ---------------------------------------------------------------------------


def test_c_d003_round_mismatch(tmp_path: Path) -> None:
    """fm round=3 但 audit 最后 round_advanced 为 round_number=2 → D003 检出。"""
    _write_topic(
        tmp_path,
        "t-round-mismatch",
        fm={"status": "open", "round": "3"},
        audit_lines=[
            json.dumps({"action": "round_advanced", "fields": {"round_number": 1}}),
            json.dumps({"action": "round_advanced", "fields": {"round_number": 2}}),
        ],
    )

    detector = scan_plane_audit(tmp_path)
    kinds = [d.kind for d in detector.drifts]
    assert "round_mismatch" in kinds


# ---------------------------------------------------------------------------
# (d) D004 close_reason_invalid
# ---------------------------------------------------------------------------


def test_d_d004_close_reason_invalid(tmp_path: Path) -> None:
    """status=closed + close_reason 不在合法枚举 → D004 检出（验证型写已拦）。"""
    _write_topic(
        tmp_path,
        "t-bad-close-reason",
        fm={"status": "closed", "close_reason": "stale_legacy_value"},
        audit_lines=[json.dumps({"action": "close"})],
    )

    detector = scan_plane_audit(tmp_path)
    kinds = [d.kind for d in detector.drifts]
    assert "close_reason_invalid" in kinds
    cr_drift = next(d for d in detector.drifts if d.kind == "close_reason_invalid")
    assert cr_drift.field == "close_reason"
    assert cr_drift.current_value == "stale_legacy_value"


# ---------------------------------------------------------------------------
# (e) D005 archive_waive_no_audit
# ---------------------------------------------------------------------------


def test_e_d005_archive_no_audit(tmp_path: Path) -> None:
    """archived=true 但 audit.jsonl 无 archive 行 → D005 检出。"""
    _write_topic(
        tmp_path,
        "t-archived-no-audit",
        fm={"status": "closed", "archived": "true"},
        audit_lines=[json.dumps({"action": "close"})],
    )

    detector = scan_plane_audit(tmp_path)
    kinds = [d.kind for d in detector.drifts]
    assert "archive_waive_no_audit" in kinds


# ---------------------------------------------------------------------------
# (f) 双格式输出 json + human 内容覆盖一致
# ---------------------------------------------------------------------------


def test_f_dual_format_output_consistency(tmp_path: Path) -> None:
    """json 与 human 输出都含 D001 漂移的核心字段 (drift_id/kind/path)。"""
    _write_topic(
        tmp_path,
        "t-clean",
        fm={"status": "open"},
    )
    _write_topic(
        tmp_path,
        "t-drift",
        fm={"status": "closed", "close_reason": "experiment_done"},
        audit_lines=[json.dumps({"action": "round_advanced"})],
    )

    detector = scan_plane_audit(tmp_path)
    assert detector.count > 0

    json_payload = verify_audit_output.render_json(
        detector, workspace_root=str(tmp_path)
    )
    human_text = verify_audit_output.render_human(
        detector, workspace_root=str(tmp_path)
    )

    # json: 含 drift_count + drifts 数组
    assert json_payload["status"] == "drift_detected"
    assert json_payload["drift_count"] == detector.count
    assert json_payload["drifts"]
    json_drift_ids = {d["drift_id"] for d in json_payload["drifts"]}

    # human: 表格里含同批 drift_id
    for did in json_drift_ids:
        assert did in human_text, f"drift_id {did} 缺失 human 输出"


# ---------------------------------------------------------------------------
# (g) 退出码
# ---------------------------------------------------------------------------


def test_g_exit_code_zero_when_clean(tmp_path: Path) -> None:
    """无漂移 + 无损坏 → exit code 0。"""
    _write_topic(tmp_path, "t-clean", fm={"status": "open"})

    detector = scan_plane_audit(tmp_path)
    assert detector.count == 0
    assert verify_audit_output.compute_exit_code(detector) == 0


def test_g_exit_code_one_when_drift(tmp_path: Path) -> None:
    """有漂移 → exit code 1。"""
    _write_topic(
        tmp_path,
        "t-drift",
        fm={"status": "closed", "close_reason": "experiment_done"},
        audit_lines=[json.dumps({"action": "round_advanced"})],
    )

    detector = scan_plane_audit(tmp_path)
    assert detector.count > 0
    assert verify_audit_output.compute_exit_code(detector) == 1


def test_g_exit_code_two_when_corrupted(tmp_path: Path) -> None:
    """audit.jsonl 损坏 → exit code 2（即使 detector.count=0 也优先）。"""
    _write_topic(
        tmp_path,
        "t-corrupt",
        fm={"status": "open"},
        audit_lines=["this is not valid json"],
    )

    # corrupted_files 由调用方检测；这里直接传 corrupted_files 模拟
    detector = DriftDetector()
    code = verify_audit_output.compute_exit_code(
        detector, corrupted_files=["map/topics/t-corrupt/audit.jsonl"]
    )
    assert code == 2


# ---------------------------------------------------------------------------
# (h) scanner CLOSE_REASON_LEGAL 与 sdk 单源同步
# ---------------------------------------------------------------------------


def test_h_close_reason_legal_synced_with_sdk() -> None:
    """scanner.CLOSE_REASON_LEGAL === sdk.map_fs.validation.CLOSE_REASON_LEGAL。

    双源必须完全一致（验证型写拒绝 + 审计层检测防线同步）；
    任何一侧加入新值，另一侧必须同步（否则 D004 漏报或误报）。
    """
    assert CLOSE_REASON_LEGAL == fs_validation.CLOSE_REASON_LEGAL
    # 与 plan §I7 §I8 验收: 4 值完整
    assert frozenset({
        "experiment_ready",
        "experiment_done",
        "cancelled",
        "discussion_converged",
    }) == CLOSE_REASON_LEGAL


# ---------------------------------------------------------------------------
# (i) drift_id 累积编号
# ---------------------------------------------------------------------------


def test_i_drift_id_increments_per_kind(tmp_path: Path) -> None:
    """多条同类漂移 → drift_id 累积编号（D001_001, D001_002）。"""
    for slug in ("t1", "t2", "t3"):
        _write_topic(
            tmp_path,
            slug,
            fm={"status": "closed", "close_reason": "experiment_done"},
            audit_lines=[json.dumps({"action": "round_advanced"})],
        )

    detector = scan_plane_audit(tmp_path)
    close_drift_ids = [
        d.drift_id for d in detector.drifts if d.kind == "close_no_audit"
    ]
    assert len(close_drift_ids) == 3
    # DriftDetector 编号格式: Dxxx_NNN，同类递增
    assert close_drift_ids[0] == "D001_001"
    assert close_drift_ids[1] == "D001_002"
    assert close_drift_ids[2] == "D001_003"


# ---------------------------------------------------------------------------
# (j) workspace 无 map/ 目录 → 0 漂移（不报错）
# ---------------------------------------------------------------------------


def test_j_empty_workspace_no_drift(tmp_path: Path) -> None:
    """workspace 无 map/topics/ 也无 map/experiments/ → scan_plane_audit 返回空 detector。"""
    detector = scan_plane_audit(tmp_path)
    assert detector.count == 0
    assert verify_audit_output.compute_exit_code(detector) == 0
