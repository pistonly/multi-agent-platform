"""T7 close_reason 枚举 + close_note 三字段校验回归测试 (实验 e6d23886 I9)。

3 case（与 plan §I9 验收对齐）：
- case_1: 旧 3 值合法路径（experiment_ready / experiment_done / cancelled）
  + close_note 自由描述语义维持兼容
- case_2: discussion_converged 新路径 + close_note 三字段完整 → 放行
- case_3: 非法值拒绝 + close_note 缺字段拒绝 + followup_gate 显式空拒绝
  （parametrize 4 路径：非法值 / 缺 experiment_id / 缺 followup_gate /
  followup_gate 显式空）

合并 SDK 契约校验（与 server/api/fs.py 409 映射逐字对齐）：
- InvalidCloseReasonError.reason / .legal
- InvalidCloseNoteError.missing / .empty_fields
"""
from __future__ import annotations

from pathlib import Path

import pytest
from map_fs import (
    CLOSE_REASON_LEGAL,
    InvalidCloseNoteError,
    InvalidCloseReasonError,
    parse_topic_dir,
    validate_close,
    write_topic_index,
)


def _topic(tmp_path: Path, *, creator: str = "host"):
    write_topic_index(tmp_path, "t", title="T", creator=creator, overwrite=True)
    return parse_topic_dir(tmp_path / "map" / "topics" / "t", tmp_path)


def test_case_1_old_three_values_with_free_close_note(tmp_path: Path) -> None:
    """旧 3 值合法路径 + close_note 自由描述语义（兼容历史 close）。"""
    topic = _topic(tmp_path)

    for reason in ("experiment_ready", "experiment_done", "cancelled"):
        fields = validate_close(
            topic,
            close_reason=reason,
            close_note="任意的自由描述文本，不强制结构化字段",
        )
        assert fields["status"] == "closed"
        assert fields["close_reason"] == reason
        assert "close_note" in fields
    # close_note 缺失也允许（旧路径）
    fields = validate_close(topic, close_reason="experiment_done")
    assert fields["status"] == "closed"
    assert "close_note" not in fields


def test_case_2_discussion_converged_with_full_close_note(tmp_path: Path) -> None:
    """discussion_converged + close_note 三字段完整 → 放行。"""
    topic = _topic(tmp_path)

    full_note = (
        "讨论已收敛。\n"
        "\n"
        "experiment_id: none\n"
        "followup_gate: 不开实验直接归档，已沉淀决策到 ARCHIVE.md\n"
        "drift_ack: 无漂移\n"
    )
    fields = validate_close(
        topic, close_reason="discussion_converged", close_note=full_note
    )
    assert fields["status"] == "closed"
    assert fields["close_reason"] == "discussion_converged"
    assert fields["close_note"] == full_note


def test_case_3_rejection_paths(tmp_path: Path) -> None:
    """非法值 / 缺字段 / followup_gate 显式空 → 拒绝。"""
    topic = _topic(tmp_path)

    # (3.1) 非法 close_reason
    with pytest.raises(InvalidCloseReasonError) as exc_info:
        validate_close(topic, close_reason="legacy_invalid")
    assert exc_info.value.reason == "legacy_invalid"
    assert sorted(exc_info.value.legal) == sorted(CLOSE_REASON_LEGAL)
    assert exc_info.value.legal == sorted(CLOSE_REASON_LEGAL)

    # (3.2) discussion_converged + close_note 完全缺失
    with pytest.raises(InvalidCloseNoteError) as exc_info:
        validate_close(topic, close_reason="discussion_converged")
    assert set(exc_info.value.missing) == {"experiment_id", "followup_gate"}

    # (3.3) discussion_converged + close_note 缺 experiment_id
    note_no_exp_id = (
        "已收敛。\n"
        "\n"
        "followup_gate: 不开实验直接归档\n"
    )
    with pytest.raises(InvalidCloseNoteError) as exc_info:
        validate_close(
            topic, close_reason="discussion_converged", close_note=note_no_exp_id
        )
    assert exc_info.value.missing == ["experiment_id"]

    # (3.4) discussion_converged + close_note 缺 followup_gate
    note_no_fg = (
        "已收敛。\n"
        "\n"
        "experiment_id: none\n"
    )
    with pytest.raises(InvalidCloseNoteError) as exc_info:
        validate_close(
            topic, close_reason="discussion_converged", close_note=note_no_fg
        )
    assert exc_info.value.missing == ["followup_gate"]

    # (3.5) discussion_converged + followup_gate 显式空字符串
    note_empty_fg = (
        "已收敛。\n"
        "\n"
        "experiment_id: none\n"
        "followup_gate: \n"
    )
    with pytest.raises(InvalidCloseNoteError) as exc_info:
        validate_close(
            topic, close_reason="discussion_converged", close_note=note_empty_fg
        )
    assert exc_info.value.empty_fields == ["followup_gate"]

    # (3.6) drift_ack 缺失合法（可选字段）
    note_no_drift_ack = (
        "已收敛。\n"
        "\n"
        "experiment_id: none\n"
        "followup_gate: 不开实验直接归档\n"
    )
    fields = validate_close(
        topic, close_reason="discussion_converged", close_note=note_no_drift_ack
    )
    assert fields["status"] == "closed"
