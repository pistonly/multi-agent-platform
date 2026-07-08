import asyncio
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
        self._calls: list[str] = []

    def whoami(self) -> dict[str, Any]:
        return {"id": f"{self.persona}-agent", "name": self.persona}

    def todos(self) -> dict[str, Any]:
        return self._todos

    def work(self) -> dict[str, Any]:
        self._calls.append("work")
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

    def experiment_scan_stalled_locks(self) -> dict[str, Any]:
        self._calls.append("scan_stalled")
        return {"notification_ids": ["n1", "n2"], "emitted_count": 2}


def test_summarize_pending_work_counts_buckets_and_notifications() -> None:
    todos = {
        "pending_topic_replies": [{"comment_id": "c1"}, {"comment_id": "c2"}],
        "stale_open_topics": [{"topic_id": "t2"}],
        "my_open_topics": [{"id": "t1"}],
    }
    notifications = [{"id": "n1"}]
    summary = summarize_pending_work(todos, notifications=notifications)
    assert summary.has_work is True
    assert summary.total_items == 4
    assert summary.todo_buckets[0].kind == "pending_topic_replies"
    assert summary.todo_buckets[0].count == 2
    assert summary.todo_buckets[1].kind == "stale_open_topics"


def test_summarize_pending_work_ignores_my_open_topics_alone() -> None:
    summary = summarize_pending_work({"my_open_topics": [{"id": "t1"}]})
    assert summary.has_work is False
    assert summary.total_items == 0


def test_summarize_pending_work_ignores_non_actionable_open_experiments() -> None:
    summary = summarize_pending_work(
        {
            "my_open_experiments": [
                {
                    "id": "archived-approved",
                    "title": "Archived approved",
                    "phase": "approved",
                    "archived_at": "2026-07-08T00:00:00Z",
                    "actions": ["start"],
                },
                {
                    "id": "waiting-result",
                    "title": "Waiting result review",
                    "phase": "result_review",
                    "blocked_on": "awaiting_result_approval",
                    "actions": [],
                },
            ]
        }
    )

    assert summary.has_work is False
    assert summary.total_items == 0


def test_summarize_pending_work_ignores_informational_only_open_experiments() -> None:
    """f873c287 I1(f): informational_only experiments must NOT enter the
    waker obligation bucket even when they appear in
    ``my_open_experiments`` (the partition still surfaces them for UI;
    only the waker obligation is suppressed).
    """
    summary = summarize_pending_work(
        {
            "my_open_experiments": [
                {
                    "id": "review-no-creator-review",
                    "title": "Awaiting non-creator review",
                    "phase": "review",
                    "blocked_on": "awaiting_non_creator_review",
                    "phase_owner": "reviewer",
                    "informational_only": True,
                    "actions": [],
                },
                {
                    "id": "result-review-waiting",
                    "title": "Awaiting reviewer result approval",
                    "phase": "result_review",
                    "blocked_on": "awaiting_result_approval",
                    "phase_owner": "reviewer",
                    "informational_only": True,
                    "actions": [],
                },
                # Counter-example: an actionable experiment that should wake.
                {
                    "id": "host-actionable",
                    "title": "Approved and ready",
                    "phase": "approved",
                    "blocked_on": "none",
                    "phase_owner": "host",
                    "informational_only": False,
                    "actions": ["start"],
                },
            ]
        }
    )

    assert summary.has_work is True
    assert summary.total_items == 1
    assert summary.todo_buckets[0].kind == "my_open_experiments"
    assert summary.todo_buckets[0].count == 1
    samples = summary.todo_buckets[0].samples
    assert len(samples) == 1
    assert "host-actionable" in samples[0]
    for suppressed in ("review-no-creator-review", "result-review-waiting"):
        assert all(suppressed not in s for s in samples), (
            f"informational_only experiment {suppressed} leaked into wake samples"
        )


def test_summarize_pending_work_wakes_on_actionable_open_experiment() -> None:
    summary = summarize_pending_work(
        {
            "my_open_experiments": [
                {
                    "id": "draft-exp",
                    "title": "Draft experiment",
                    "phase": "draft",
                    "actions": ["submit_for_review"],
                }
            ]
        }
    )

    assert summary.has_work is True
    assert summary.total_items == 1
    assert summary.todo_buckets[0].kind == "my_open_experiments"
    prompt = build_remind_prompt("host", summary)
    assert "Draft experiment" in prompt
    assert "phase=draft" in prompt
    assert "submit_for_review" in prompt
    assert "experiment-host" in prompt


def test_build_wake_context_topic_progress_triggers_wake() -> None:
    from cli.simple_waker import build_remind_prompt, build_wake_context

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


def test_build_wake_context_drain_topics_wakes_on_open_topics() -> None:
    from cli.simple_waker import build_remind_prompt, build_wake_context

    context = build_wake_context(
        topic_progress_data={"items": [], "total": 0},
        todos={},
        persona="host",
        drain_topics=True,
        open_topics=[
            {
                "id": "t1",
                "title": "Open topic",
                "discussion_round": "round1",
                "comment_count": 0,
            }
        ],
    )

    assert context.has_work is True
    assert context.open_topic_count == 1
    prompt = build_remind_prompt("host", context)
    assert "Drain topics 模式" in prompt
    assert "主动推动话题进展" in prompt
    assert "积极解决问题" in prompt
    assert "topic-host Skill" in prompt
    assert "行动项" in prompt
    assert "Open topic" in prompt


def test_summarize_pending_work_idle_when_empty() -> None:
    summary = summarize_pending_work({}, notifications=[])
    assert summary.has_work is False
    assert summary.total_items == 0


def test_map_command_client_work_requests_wakeable(monkeypatch) -> None:
    seen: dict[str, list[str]] = {}

    def fake_run(self, args, *, parse_yaml=True, retryable=False):
        seen["args"] = args
        return {}

    monkeypatch.setattr(MapCommandClient, "_run", fake_run)
    MapCommandClient(persona="host").work()
    assert seen["args"] == ["work", "--notification-category", "wakeable"]


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
        {
            "pending_topic_replies": [{"comment_id": "c1"}],
            "stale_open_topics": [{"topic_id": "t1"}],
        },
        notifications=[{"id": "n1"}],
    )
    prompt = build_remind_prompt("host", summary)
    assert "MAP 协作提醒 · host" in prompt
    assert "pending_topic_replies: 1" in prompt
    assert "stale_open_topics: 1" in prompt
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
    assert "scan_stalled" not in client._calls
    backend.wake_async.assert_not_called()


def test_non_host_run_once_does_not_scan_stalled_locks(tmp_path: Path) -> None:
    client = FakeMapClient(persona="participant", todos={})
    backend = MagicMock()
    backend.wake_async = AsyncMock()
    config = SimpleWakerConfig(
        persona="participant",
        project_root=tmp_path,
        state_file=tmp_path / "state.json",
    )
    waker = SimpleWaker(client=client, config=config, backend=backend)

    stats = waker.run_once()

    assert stats.polls_idle == 1
    assert "scan_stalled" not in client._calls
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


def test_runtime_contract_change_resets_existing_session(tmp_path: Path) -> None:
    client = FakeMapClient(persona="host", todos={})
    backend = MagicMock()
    backend.reset_session = AsyncMock()
    backend.wake_async = AsyncMock()
    config = SimpleWakerConfig(
        persona="host",
        project_root=tmp_path,
        state_file=tmp_path / "state.json",
    )
    waker = SimpleWaker(client=client, config=config, backend=backend)
    persona_state = waker._persona_state("host")
    persona_state["claude_session_id"] = "old-session"

    asyncio.run(waker._reset_runtime_session_if_contract_changed())

    backend.reset_session.assert_awaited_once()
    assert persona_state["runtime_contract_hash"] == waker._runtime_contract_hash
    assert persona_state["runtime_contract_version"] == simple_waker.RUNTIME_CONTRACT_VERSION


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
    assert stats.stalled_lock_notifications == 2
    assert client._calls[:2] == ["scan_stalled", "work"]
    backend.wake_async.assert_awaited_once()


def test_run_once_drain_topics_sends_remind_for_open_topics(tmp_path: Path) -> None:
    client = FakeMapClient(persona="host", todos={})
    client.topic_list_open = MagicMock(return_value=[{"id": "t1", "title": "T"}])  # type: ignore[method-assign]
    backend = MagicMock()
    backend.wake_async = AsyncMock(return_value=MagicMock(session_id="sess-1", skipped=False))
    config = SimpleWakerConfig(
        persona="host",
        project_root=tmp_path,
        drain_topics=True,
        state_file=tmp_path / "state.json",
    )
    waker = SimpleWaker(client=client, config=config, backend=backend)

    stats = waker.run_once()

    assert stats.reminds_sent == 1
    client.topic_list_open.assert_called_once()
    prompt = backend.wake_async.await_args.kwargs["prompt"]
    assert "Drain topics 模式" in prompt
    assert "主动推动话题进展" in prompt
    assert "topic-host Skill" in prompt
    assert "T" in prompt


def test_simple_waker_config_stale_threshold_defaults_to_none() -> None:
    """f873c287 I1(g): the new ``stale_threshold_minutes`` field defaults to
    ``None`` so legacy callers that do not opt-in keep the server's
    default (30 minutes via Settings / env var).
    """
    config = SimpleWakerConfig()
    assert config.stale_threshold_minutes is None


def test_simple_waker_run_command_exports_stale_threshold_env(
    monkeypatch, tmp_path: Path
) -> None:
    """f873c287 I1(g): ``--waker-stale-threshold`` CLI flag exports
    ``MAP_STALE_OPEN_TOPIC_THRESHOLD_MINUTES`` so subprocess ``map work``
    invocations and any in-process server pick up the override via
    ``server.config.Settings``.

    The override must reach the env BEFORE the first ``get_settings()``
    call (the Settings singleton is ``@lru_cache``); this test also
    forces a ``cache_clear()`` to make sure the new value wins even if
    Settings was already loaded.
    """
    import os

    from typer.testing import CliRunner

    # Pre-clear the env so we can observe the flag-driven write.
    monkeypatch.delenv("MAP_STALE_OPEN_TOPIC_THRESHOLD_MINUTES", raising=False)
    # Touch server.config so the lru_cache has a stale entry; the flag
    # should still overwrite it after cache_clear.
    from server.config import get_settings

    get_settings.cache_clear()
    stale = get_settings().stale_open_topic_threshold_minutes
    assert stale == 30  # default before any env override

    # Block run_forever so the CLI command returns immediately.
    monkeypatch.setattr(
        "cli.simple_waker.SimpleWaker.run_forever",
        lambda self: None,
    )

    runner = CliRunner()
    result = runner.invoke(
        simple_waker.APP,
        [
            "--persona",
            "host",
            "--project-root",
            str(tmp_path),
            "--waker-stale-threshold",
            "5",
            "--once",
        ],
    )
    assert result.exit_code == 0, result.output

    assert os.environ.get("MAP_STALE_OPEN_TOPIC_THRESHOLD_MINUTES") == "5"
    # After the CLI applied the override and cleared the cache, a fresh
    # ``get_settings()`` returns the new threshold.
    assert get_settings().stale_open_topic_threshold_minutes == 5

