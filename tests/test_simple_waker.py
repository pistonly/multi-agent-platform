import importlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

simple_waker = importlib.import_module("cli.simple_waker")
MapCommandClient = importlib.import_module("cli.map_command_client").MapCommandClient
SimpleWaker = simple_waker.SimpleWaker
SimpleWakerConfig = simple_waker.SimpleWakerConfig
build_remind_prompt = simple_waker.build_remind_prompt
next_sleep_seconds = simple_waker.next_sleep_seconds
should_send_remind = simple_waker.should_send_remind
summarize_pending_work = simple_waker.summarize_pending_work


class FakeMapClient(MapCommandClient):
    def __init__(
        self,
        *,
        persona: str,
        todos: dict[str, Any],
        notifications: list[dict[str, Any]] | None = None,
        topic_progress: dict[str, Any] | None = None,
    ) -> None:
        self.persona = persona
        self._todos = todos
        self._notifications = notifications or []
        self._topic_progress = topic_progress or {"items": [], "total": 0}

    def whoami(self) -> dict[str, Any]:
        return {"id": f"{self.persona}-agent", "name": self.persona}

    def todos(self) -> dict[str, Any]:
        return self._todos

    def work(self) -> dict[str, Any]:
        return {
            "agent": self.whoami(),
            "topic_progress": self._topic_progress,
            "todos": self._todos,
            "notifications": {
                "items": self._notifications,
                "total": len(self._notifications),
                "unread_count": len(self._notifications),
            },
        }

    def notifications_unread(self) -> list[dict[str, Any]]:
        return self._notifications

    # v0.10：simple-waker remind 后写聚合 inbound_event + action_item escalation。
    # 测试 stub 不实际调 CLI，返回 True（已记录）/ None 即可。
    def inbound_event_record(
        self,
        *,
        event_id: str,
        fingerprint: str,
        event_type: str,
        source: str = "polling",
    ) -> bool:
        self._inbound_events = getattr(self, "_inbound_events", [])
        self._inbound_events.append(
            {
                "event_id": event_id,
                "fingerprint": fingerprint,
                "event_type": event_type,
                "source": source,
            }
        )
        return True

    def action_mark_wake_sent(self, action_item_id: str) -> dict[str, Any] | None:
        self._wake_sent = getattr(self, "_wake_sent", [])
        self._wake_sent.append(action_item_id)
        return {"id": action_item_id, "ok": True}

    def action_mark_stale(self, action_item_id: str) -> dict[str, Any] | None:
        self._stale = getattr(self, "_stale", [])
        self._stale.append(action_item_id)
        return {"id": action_item_id, "ok": True}


def test_summarize_pending_work_counts_buckets_and_notifications() -> None:
    todos = {
        "pending_topic_replies": [{"comment_id": "c1"}, {"comment_id": "c2"}],
        "my_open_topics": [{"id": "t1"}],
    }
    notifications = [{"id": "n1"}]
    summary = summarize_pending_work(todos, notifications=notifications)
    assert summary.has_work is True
    assert summary.total_items == 3
    assert summary.todo_buckets[0].kind == "pending_topic_replies"
    assert summary.todo_buckets[0].count == 2


def test_summarize_pending_work_ignores_my_open_topics_alone() -> None:
    summary = summarize_pending_work({"my_open_topics": [{"id": "t1"}]})
    assert summary.has_work is False
    assert summary.total_items == 0


def test_build_wake_context_topic_progress_triggers_wake() -> None:
    from cli.simple_waker import build_wake_context, build_remind_prompt

    context = build_wake_context(
        topic_progress_data={
            "items": [
                {
                    "topic_id": "t1",
                    "topic_title": "Dogfood",
                    "discussion_round": "round2",
                    "last_comment_author_name": "multi-agents-platform-host",
                    "new_comment_count": 1,
                    "new_comments": [
                        {
                            "author_name": "multi-agents-platform-host",
                            "excerpt": "Round 2 kickoff",
                        }
                    ],
                    "work_items": [{"kind": "unread_change", "priority": "contextual"}],
                }
            ],
            "total": 1,
        },
        todos={},
    )
    assert context.has_work is True
    prompt = build_remind_prompt("participant", context)
    assert "话题 work items" in prompt
    assert "Dogfood" in prompt
    assert "topic progress" in prompt


def test_build_wake_context_host_caught_up_when_no_progress_and_no_todos() -> None:
    from cli.simple_waker import build_wake_context

    context = build_wake_context(
        topic_progress_data={"items": [], "total": 0},
        todos={"my_open_topics": [{"id": "t1"}]},
    )
    assert context.has_work is False


def test_summarize_pending_work_idle_when_empty() -> None:
    summary = summarize_pending_work({}, notifications=[])
    assert summary.has_work is False
    assert summary.total_items == 0


def test_should_send_remind_requires_work_and_respects_cooldown() -> None:
    summary = summarize_pending_work({"mentions": [{"id": "m1"}]})
    now = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    ok, reason = should_send_remind(
        summary,
        now=now,
        last_remind_at=None,
        inflight=False,
        min_remind_seconds=30,
    )
    assert ok is True
    assert reason is None

    ok, reason = should_send_remind(
        summary,
        now=now,
        last_remind_at=now - timedelta(seconds=10),
        inflight=False,
        min_remind_seconds=30,
    )
    assert ok is False
    assert reason == "cooldown"

    ok, reason = should_send_remind(
        summarize_pending_work({}),
        now=now,
        last_remind_at=None,
        inflight=False,
        min_remind_seconds=30,
    )
    assert ok is False
    assert reason == "idle"


def test_should_send_remind_skips_when_inflight() -> None:
    summary = summarize_pending_work({"mentions": [{"id": "m1"}]})
    ok, reason = should_send_remind(
        summary,
        now=datetime.now(UTC),
        last_remind_at=None,
        inflight=True,
        min_remind_seconds=30,
    )
    assert ok is False
    assert reason == "busy"


def test_build_remind_prompt_lists_buckets() -> None:
    summary = summarize_pending_work(
        {"pending_topic_replies": [{"comment_id": "c1"}]},
        notifications=[{"id": "n1"}],
    )
    prompt = build_remind_prompt("host", summary)
    assert "MAP 协作提醒 · host" in prompt
    assert "pending_topic_replies: 1" in prompt
    assert "notification: 1" in prompt
    assert "topic progress" in prompt


def test_next_sleep_seconds_active_vs_idle() -> None:
    config = SimpleWakerConfig(active_interval=30.0, idle_interval=300.0)
    busy = summarize_pending_work({"mentions": [{"id": "m1"}]})
    idle = summarize_pending_work({})
    assert next_sleep_seconds(busy, config) == 30.0
    assert next_sleep_seconds(idle, config) == 300.0


def test_run_once_dry_run_does_not_wake_backend(tmp_path: Path) -> None:
    client = FakeMapClient(
        persona="host",
        todos={"pending_topic_replies": [{"comment_id": "c1", "topic_id": "t1"}]},
    )
    backend = MagicMock()
    backend.wake_async = AsyncMock()
    config = SimpleWakerConfig(
        persona="host",
        project_root=tmp_path,
        dry_run=True,
        state_file=tmp_path / "state.json",
    )
    waker = SimpleWaker(client=client, config=config, backend=backend)
    stats = waker.run_once()
    assert stats.dry_run_actions == 1
    assert stats.reminds_sent == 0
    backend.wake_async.assert_not_called()


def test_run_forever_once_dry_run_skips_backend_connect(tmp_path: Path) -> None:
    client = FakeMapClient(
        persona="host",
        todos={"pending_topic_replies": [{"comment_id": "c1", "topic_id": "t1"}]},
    )
    backend = MagicMock()
    backend.connect = AsyncMock()
    backend.disconnect = AsyncMock()
    backend.wake_async = AsyncMock()
    config = SimpleWakerConfig(
        persona="host",
        project_root=tmp_path,
        dry_run=True,
        once=True,
        state_file=tmp_path / "state.json",
    )
    waker = SimpleWaker(client=client, config=config, backend=backend)
    stats = waker.run_forever()
    assert stats.cycles == 1
    assert stats.dry_run_actions == 1
    assert stats.reminds_sent == 0
    backend.connect.assert_not_called()
    backend.disconnect.assert_not_called()
    backend.wake_async.assert_not_called()


def test_run_once_sends_remind_when_work_exists(tmp_path: Path) -> None:
    client = FakeMapClient(
        persona="host",
        todos={},
        topic_progress={
            "items": [
                {
                    "topic_id": "t1",
                    "topic_title": "T",
                    "discussion_round": "round2",
                    "last_comment_author_name": "participant",
                    "new_comment_count": 1,
                    "new_comments": [{"author_name": "participant", "excerpt": "hi"}],
                    "work_items": [{"kind": "unread_change", "priority": "contextual"}],
                }
            ],
            "total": 1,
        },
    )
    backend = MagicMock()
    backend.wake_async = AsyncMock(return_value=MagicMock(session_id="sess-1", skipped=False))
    backend.connect = AsyncMock()
    backend.disconnect = AsyncMock()
    config = SimpleWakerConfig(
        persona="host",
        project_root=tmp_path,
        state_file=tmp_path / "state.json",
    )
    waker = SimpleWaker(client=client, config=config, backend=backend)
    stats = waker.run_once()
    assert stats.reminds_sent == 1
    backend.wake_async.assert_awaited_once()
    prompt = backend.wake_async.await_args.kwargs["prompt"]
    assert "话题 work items" in prompt
