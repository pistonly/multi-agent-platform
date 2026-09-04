"""交互会话桥接核心单测（实验 db97aeac I1 / A2+A4 状态机部分）。

覆盖：fingerprint 稳定性与 obligation 过滤、has_obligation_work、
evaluate_remind 的 new/repeat/cooldown/silenced 四分支、last_seen_at 心跳、
record_remind 计数重置与递增、state 文件读写与损坏兜底。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cli.errors import WorkerError
from cli.interactive_bridge import (
    bridge_state_path,
    evaluate_remind,
    has_obligation_work,
    load_state,
    record_remind,
    record_trigger,
    save_state,
    work_fingerprint,
)

NOW = datetime(2026, 9, 4, 8, 0, 0, tzinfo=timezone.utc)


def _work(*, obligation_keys=(), contextual_keys=(), todo_counts=None, unread=0, notif_ids=()):
    return {
        "topic_progress": {
            "items": [
                {
                    "work_items": [
                        *[
                            {"kind": "stale_open_topics", "priority": "obligation", "idempotency_key": k}
                            for k in obligation_keys
                        ],
                        *[
                            {"kind": "unread_change", "priority": "contextual", "idempotency_key": k}
                            for k in contextual_keys
                        ],
                    ]
                }
            ]
        },
        "todos": todo_counts or {},
        "notifications": {
            "items": [{"id": n} for n in notif_ids],
            "unread_count": unread,
        },
    }


# ---- fingerprint ------------------------------------------------------------


def test_fingerprint_stable_for_same_work():
    assert work_fingerprint(_work(obligation_keys=["k1"])) == work_fingerprint(
        _work(obligation_keys=["k1"])
    )


def test_fingerprint_changes_with_obligation_set():
    assert work_fingerprint(_work(obligation_keys=["k1"])) != work_fingerprint(
        _work(obligation_keys=["k1", "k2"])
    )


def test_fingerprint_ignores_contextual_items():
    assert work_fingerprint(_work()) == work_fingerprint(_work(contextual_keys=["ctx1"]))


def test_fingerprint_covers_todos_and_notifications():
    base = _work()
    assert work_fingerprint(base) != work_fingerprint(_work(todo_counts={"mentions": [{}]}))
    assert work_fingerprint(base) != work_fingerprint(
        _work(unread=1, notif_ids=["2299521c-0dee"])
    )


# ---- has_obligation_work ----------------------------------------------------


def test_has_obligation_work_false_for_contextual_only():
    assert has_obligation_work(_work(contextual_keys=["ctx1"])) is False


def test_has_obligation_work_true_for_each_source():
    assert has_obligation_work(_work(obligation_keys=["k1"])) is True
    assert has_obligation_work(_work(todo_counts={"action_items": [{}]})) is True
    assert has_obligation_work(_work(unread=2)) is True


# ---- evaluate_remind 四分支 --------------------------------------------------


def test_new_fingerprint_reminds_with_block():
    decision = evaluate_remind({}, fingerprint="fp1", now=NOW)
    assert decision == {"remind": True, "block": True, "escalation": 1, "reason": "new"}


def test_same_fingerprint_within_cooldown_suppressed():
    state = {"fingerprint": "fp1", "remind_count": 1, "last_reminded_at": NOW.isoformat()}
    decision = evaluate_remind(
        state, fingerprint="fp1", now=NOW + timedelta(seconds=60), min_remind_seconds=900
    )
    assert decision["remind"] is False and decision["reason"] == "cooldown"


def test_same_fingerprint_after_cooldown_repeats_without_block():
    state = {"fingerprint": "fp1", "remind_count": 1, "last_reminded_at": NOW.isoformat()}
    decision = evaluate_remind(
        state, fingerprint="fp1", now=NOW + timedelta(seconds=901), min_remind_seconds=900
    )
    assert decision == {"remind": True, "block": False, "escalation": 2, "reason": "repeat"}


def test_max_remind_count_silences():
    state = {"fingerprint": "fp1", "remind_count": 3, "last_reminded_at": NOW.isoformat()}
    decision = evaluate_remind(
        state,
        fingerprint="fp1",
        now=NOW + timedelta(hours=2),
        min_remind_seconds=900,
        max_remind_count=3,
    )
    assert decision["remind"] is False and decision["reason"] == "silenced"


def test_naive_last_reminded_at_treated_as_utc():
    state = {"fingerprint": "fp1", "remind_count": 1, "last_reminded_at": "2026-09-04T08:00:00"}
    decision = evaluate_remind(
        state, fingerprint="fp1", now=NOW + timedelta(seconds=30), min_remind_seconds=900
    )
    assert decision["reason"] == "cooldown"


# ---- state 读写与心跳 --------------------------------------------------------


def test_record_trigger_writes_heartbeat_and_runtime():
    state = record_trigger({}, now=NOW, runtime="claude-code")
    assert state["last_seen_at"] == NOW.isoformat()
    assert state["runtime"] == "claude-code"
    # 已存在的 runtime 不被覆盖
    assert record_trigger(state, now=NOW, runtime="other")["runtime"] == "claude-code"


def test_record_remind_resets_count_on_new_fingerprint():
    state = {"fingerprint": "old", "remind_count": 3}
    state = record_remind(
        state, fingerprint="new", now=NOW, decision={"escalation": 1}
    )
    assert state["fingerprint"] == "new"
    assert state["remind_count"] == 1
    assert state["first_reminded_at"] == NOW.isoformat()
    assert state["last_reminded_at"] == NOW.isoformat()


def test_record_remind_increments_on_same_fingerprint():
    state = {"fingerprint": "fp1", "remind_count": 1}
    state = record_remind(state, fingerprint="fp1", now=NOW, decision={"escalation": 2})
    assert state["remind_count"] == 2
    assert state["last_escalation"] == 2


def test_state_roundtrip_and_corrupt_fallback(tmp_path: Path):
    path = tmp_path / ".map" / "interactive-bridge-state-host.json"
    save_state(path, record_trigger({}, now=NOW, runtime="claude-code"))
    loaded = load_state(path)
    assert loaded["last_seen_at"] == NOW.isoformat()
    assert loaded["runtime"] == "claude-code"

    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(WorkerError):
        load_state(path)


def test_bridge_state_path_layout():
    path = bridge_state_path(Path("/repo"), "participant")
    assert path == Path("/repo/.map/interactive-bridge-state-participant.json")
