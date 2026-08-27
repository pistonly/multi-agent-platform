"""map_fs.validation 共享门禁纯单测（无 server、无 CLI）。

server 与 CLI local plane 的单一真值；错误消息字符串与
``server/api/fs.py`` 409 映射逐字对齐（契约见 validation 模块 docstring）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from map_fs import (
    AckPendingError,
    FsActionItem,
    OpenActionItemsError,
    TopicOwnerError,
    TopicStateError,
    parse_topic_dir,
    update_topic_index,
    validate_advance_round,
    validate_close,
    write_action_items,
    write_round_comment,
    write_topic_index,
)


def _topic(tmp_path: Path, *, creator: str = "host", participants: list[str] | None = None):
    write_topic_index(tmp_path, "t", title="T", creator=creator, participants=participants)
    return _parse(tmp_path)


def _parse(tmp_path: Path):
    """只读重解析（write_topic_index 是覆盖写，重跑会重置 index 状态）。"""
    return parse_topic_dir(tmp_path / "map" / "topics" / "t", tmp_path)


def test_advance_ok_round_field(tmp_path: Path) -> None:
    _topic(tmp_path, participants=["participant"])
    write_round_comment(tmp_path, "t", round_number=1, persona="participant", body="ok")
    assert validate_advance_round(_topic(tmp_path)) == {"round": "round2"}


def test_advance_ack_pending_missing_file_no_reason(tmp_path: Path) -> None:
    _topic(tmp_path, participants=["participant", "reviewer"])
    write_round_comment(tmp_path, "t", round_number=1, persona="participant", body="ok")
    with pytest.raises(AckPendingError) as exc_info:
        validate_advance_round(_topic(tmp_path))
    exc = exc_info.value
    assert exc.missing == ["reviewer"]
    assert str(exc) == "round ack pending: reviewer"
    # 纯缺文件的 persona 不进 missing_reasons（missing 列表已指认其名）
    assert exc.missing_reasons == {}


def test_advance_ack_pending_reason_pinpoints_file(tmp_path: Path) -> None:
    _topic(tmp_path, participants=["participant"])
    path = write_round_comment(tmp_path, "t", round_number=1, persona="participant", body="ok")
    # 手写坏文件：frontmatter author 与 persona 不符
    path.write_text(
        "---\nauthor: host\nround: 1\nkind: user\n---\nbody\n", encoding="utf-8"
    )
    with pytest.raises(AckPendingError) as exc_info:
        validate_advance_round(_topic(tmp_path))
    exc = exc_info.value
    assert exc.missing == ["participant"]
    assert exc.missing_reasons == {
        "participant": "round1-participant.md: frontmatter author=host, expected participant"
    }


def test_advance_waive_fields(tmp_path: Path) -> None:
    _topic(tmp_path, participants=["participant"])
    # waive_reason 仅在 waive_ack and waive_reason 时进 fields
    assert validate_advance_round(_topic(tmp_path), waive_ack=True, waive_reason="no signal") == {
        "round": "round2",
        "waive_reason": "no signal",
    }
    assert validate_advance_round(_topic(tmp_path), waive_ack=True) == {"round": "round2"}


def test_advance_mark_ready(tmp_path: Path) -> None:
    _topic(tmp_path, participants=["participant"])
    write_round_comment(tmp_path, "t", round_number=1, persona="participant", body="ok")
    assert validate_advance_round(_topic(tmp_path), mark_ready=True) == {"round": "ready"}


def test_advance_closed_topic_exact_message(tmp_path: Path) -> None:
    _topic(tmp_path)
    update_topic_index(tmp_path, "t", content_root="map", status="closed")
    with pytest.raises(TopicStateError) as exc_info:
        validate_advance_round(_parse(tmp_path))
    assert str(exc_info.value) == "fs topic 't' is closed; only open topics advance"


def test_advance_owner_gate(tmp_path: Path) -> None:
    topic = _topic(tmp_path)
    with pytest.raises(TopicOwnerError) as exc_info:
        validate_advance_round(topic, actor="participant")
    assert "only the topic creator 'host' can advance-round" in str(exc_info.value)
    assert "'participant'" in str(exc_info.value)
    # creator 本人通过（ack 豁免）
    assert validate_advance_round(topic, actor="host", waive_ack=True) == {"round": "round2"}


def test_close_ok_fields(tmp_path: Path) -> None:
    assert validate_close(_topic(tmp_path), close_reason="done", close_note="note") == {
        "status": "closed",
        "close_reason": "done",
        "close_note": "note",
    }
    assert validate_close(_topic(tmp_path)) == {"status": "closed"}


def test_close_already_closed_exact_message(tmp_path: Path) -> None:
    _topic(tmp_path)
    update_topic_index(tmp_path, "t", content_root="map", status="closed")
    with pytest.raises(TopicStateError) as exc_info:
        validate_close(_parse(tmp_path))
    assert str(exc_info.value) == "fs topic 't' is already closed"


def test_close_open_action_items(tmp_path: Path) -> None:
    _topic(tmp_path)
    write_action_items(
        tmp_path, "t", [FsActionItem(id=1, title="fix", owner="host", status="open")]
    )
    with pytest.raises(OpenActionItemsError) as exc_info:
        validate_close(_topic(tmp_path))
    exc = exc_info.value
    assert [i.id for i in exc.items] == [1]
    assert str(exc) == "action items open: #1 fix"


def test_close_action_items_done_passes(tmp_path: Path) -> None:
    _topic(tmp_path)
    write_action_items(
        tmp_path,
        "t",
        [FsActionItem(id=1, title="fix", owner="host", status="done", evidence="commit abc")],
    )
    assert validate_close(_topic(tmp_path)) == {"status": "closed"}


def test_close_unparseable_action_items(tmp_path: Path) -> None:
    _topic(tmp_path)
    (tmp_path / "map" / "topics" / "t" / "action-items.yaml").write_text(
        "items: [unclosed", encoding="utf-8"
    )
    with pytest.raises(OpenActionItemsError) as exc_info:
        validate_close(_topic(tmp_path))
    exc = exc_info.value
    assert exc.items == []
    assert exc.detail
    assert str(exc).startswith("action items unparseable: ")


def test_close_owner_gate(tmp_path: Path) -> None:
    with pytest.raises(TopicOwnerError) as exc_info:
        validate_close(_topic(tmp_path), actor="reviewer")
    assert "only the topic creator 'host' can close" in str(exc_info.value)


def test_dynamic_speaker_not_in_ack_list(tmp_path: Path) -> None:
    """D3：名单外 persona 的手写发言文件不阻塞 advance（不进 ack 名单）。"""
    _topic(tmp_path, participants=["participant"])
    write_round_comment(tmp_path, "t", round_number=1, persona="participant", body="ok")
    # 直接手写绕过 write_round_comment 的「发言即参与」并入逻辑
    (tmp_path / "map" / "topics" / "t" / "round1-stray.md").write_text(
        "---\nauthor: stray\nround: 1\nkind: user\n---\nlate\n", encoding="utf-8"
    )
    topic = _topic(tmp_path)
    assert "stray" not in topic.ack_participants()
    assert validate_advance_round(topic) == {"round": "round2"}
