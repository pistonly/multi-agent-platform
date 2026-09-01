import asyncio
import importlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cli.bridge_state import load_bridge_state, save_bridge_state

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

    def agent_heartbeat(self, *, busy_since: Any) -> None:
        """实验 b3ec2e4d I5：noop 默认实现。

        既有的 simple_waker 测试用 FakeMapClient 时不需要真的 PATCH server
        心跳列——只验证 wake_async 等主路径。I5 专属 busy 测试用
        ``_RecordingHeartbeatClient`` 替换它。
        """
        return None

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
                    "last_comment_author_name": "multi-agent-platform-host",
                    "new_comment_count": 1,
                    "new_comments": [
                        {
                            "author_name": "multi-agent-platform-host",
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
    assert seen["args"] == ["work", "--notification-category", "wakeable", "--client", "waker"]


def test_should_send_remind_requires_work_and_respects_cooldown() -> None:
    summary = summarize_pending_work({"mentions": [{"id": "m1"}]})
    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
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
        now=datetime.now(timezone.utc),
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


def test_build_wake_context_topk_limits_and_prioritizes() -> None:
    """top-K 配额:批量话题义务涌入时,单次 context 只保留前 K 个。

    排序 = obligation 优先 → 义务时间老→新;超出部分计入 deferred,
    prompt 中注明「下轮自动到」。"""
    from cli.simple_waker import build_remind_prompt, build_wake_context

    def _item(tid: str, priority: str, created: str) -> dict[str, Any]:
        return {
            "topic_id": tid,
            "topic_title": f"topic-{tid}",
            "discussion_round": "round1",
            "new_comment_count": 0,
            "work_items": [{"kind": "pending_topic_reply", "priority": priority, "created_at": created}],
        }

    data = {
        "items": [
            _item("new-obligation", "obligation", "2026-08-24T10:00:00+00:00"),
            _item("oldest-obligation", "obligation", "2026-08-23T08:00:00+00:00"),
            _item("mid-obligation", "obligation", "2026-08-23T20:00:00+00:00"),
            _item("contextual-1", "contextual", "2026-08-23T01:00:00+00:00"),
            _item("contextual-2", "contextual", "2026-08-24T09:00:00+00:00"),
        ],
        "total": 5,
    }

    context = build_wake_context(topic_progress_data=data, todos={}, max_prompt_topics=3)
    assert len(context.topic_progress) == 3
    assert context.topic_deferred_count == 2
    # obligation 全部优先于 contextual;obligation 内按时间老→新
    assert [e.topic_id for e in context.topic_progress] == [
        "oldest-obligation",
        "mid-obligation",
        "new-obligation",
    ]
    prompt = build_remind_prompt("participant", context)
    assert "另有 2 个话题义务本轮未列出" in prompt

    # K=0 / None = 不限制(旧行为)
    unlimited = build_wake_context(topic_progress_data=data, todos={}, max_prompt_topics=0)
    assert len(unlimited.topic_progress) == 5
    assert unlimited.topic_deferred_count == 0

    # 数量未超 K 时零 deferred,行为与旧版一致
    small = build_wake_context(
        topic_progress_data={"items": data["items"][:2], "total": 2}, todos={}, max_prompt_topics=3
    )
    assert len(small.topic_progress) == 2
    assert small.topic_deferred_count == 0


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


def test_runtime_backend_change_resets_existing_session(tmp_path: Path) -> None:
    client = FakeMapClient(persona="host", todos={})
    backend = MagicMock()
    backend.reset_session = AsyncMock()
    config = SimpleWakerConfig(
        persona="host",
        project_root=tmp_path,
        runtime="cursor",
        state_file=tmp_path / "state.json",
    )
    waker = SimpleWaker(client=client, config=config, backend=backend)
    persona_state = waker._persona_state("host")
    persona_state["runtime_backend"] = "claude"
    persona_state["claude_session_id"] = "old-session"

    asyncio.run(waker._reset_runtime_session_if_backend_changed())

    backend.reset_session.assert_awaited_once()


def test_runtime_backend_unchanged_does_not_reset(tmp_path: Path) -> None:
    client = FakeMapClient(persona="host", todos={})
    backend = MagicMock()
    backend.reset_session = AsyncMock()
    config = SimpleWakerConfig(
        persona="host",
        project_root=tmp_path,
        runtime="cursor",
        state_file=tmp_path / "state.json",
    )
    waker = SimpleWaker(client=client, config=config, backend=backend)
    waker._persona_state("host")["runtime_backend"] = "cursor"

    asyncio.run(waker._reset_runtime_session_if_backend_changed())

    backend.reset_session.assert_not_called()


def _topic_progress(*topic_ids: str) -> dict[str, Any]:
    """构造 topic_progress work 快照：每个 topic id 一条 unread_change。"""
    return {
        "items": [
            {
                "topic_id": tid,
                "topic_title": f"T-{tid}",
                "discussion_round": "round1",
                "last_comment_author_name": "participant",
                "new_comment_count": 1,
                "new_comments": [{"author_name": "participant", "excerpt": "hi"}],
                "work_items": [{"kind": "unread_change", "priority": "contextual"}],
            }
            for tid in topic_ids
        ],
        "total": len(topic_ids),
    }


def _waker_with_backend(tmp_path: Path, **topic_kwargs: Any) -> tuple[SimpleWaker, MagicMock]:
    client = FakeMapClient(persona="host", **topic_kwargs)
    backend = MagicMock()
    backend.wake_async = AsyncMock(return_value=MagicMock(session_id="sess-new", skipped=False))
    backend.reset_session = AsyncMock()
    backend.connect = AsyncMock()
    backend.disconnect = AsyncMock()
    config = SimpleWakerConfig(
        persona="host",
        project_root=tmp_path,
        state_file=tmp_path / "state.json",
    )
    return SimpleWaker(client=client, config=config, backend=backend), backend


def test_topic_switch_resets_session_before_wake(tmp_path: Path) -> None:
    """话题切换（t1 -> t2）且将 resume 旧会话 → 先 reset_session 再唤醒。"""
    waker, backend = _waker_with_backend(
        tmp_path, todos={}, topic_progress=_topic_progress("t2")
    )
    persona_state = waker._persona_state("host")
    persona_state["claude_session_id"] = "old-session"
    persona_state["last_wake_topic_ids"] = ["t1"]

    stats = waker.run_once()

    backend.reset_session.assert_awaited_once()
    backend.wake_async.assert_awaited_once()
    assert stats.session_resets_topic_switch == 1
    assert stats.reminds_sent == 1
    assert persona_state["last_wake_topic_ids"] == ["t2"]


def test_same_topic_set_does_not_reset(tmp_path: Path) -> None:
    """同一话题的新一轮（id 集合不变，仅评论推进）→ 保留会话连续性。"""
    waker, backend = _waker_with_backend(
        tmp_path, todos={}, topic_progress=_topic_progress("t1")
    )
    persona_state = waker._persona_state("host")
    persona_state["claude_session_id"] = "old-session"
    persona_state["last_wake_topic_ids"] = ["t1"]

    stats = waker.run_once()

    backend.reset_session.assert_not_called()
    assert stats.session_resets_topic_switch == 0
    assert stats.reminds_sent == 1
    assert persona_state["last_wake_topic_ids"] == ["t1"]


def test_topic_closed_resets_session(tmp_path: Path) -> None:
    """话题关闭出队（{t1} -> 空，仅 todo 工作）→ 重置会话。"""
    waker, backend = _waker_with_backend(
        tmp_path, todos={"pending_topic_replies": [{"comment_id": "c1"}]}
    )
    persona_state = waker._persona_state("host")
    persona_state["claude_session_id"] = "old-session"
    persona_state["last_wake_topic_ids"] = ["t1"]

    stats = waker.run_once()

    backend.reset_session.assert_awaited_once()
    assert stats.session_resets_topic_switch == 1
    assert persona_state["last_wake_topic_ids"] == []


def test_no_existing_session_skips_reset_on_switch(tmp_path: Path) -> None:
    """无 claude_session_id（唤醒本身就是新会话）→ 话题不同也不重置。"""
    waker, backend = _waker_with_backend(
        tmp_path, todos={}, topic_progress=_topic_progress("t2")
    )
    waker._persona_state("host")["last_wake_topic_ids"] = ["t1"]

    stats = waker.run_once()

    backend.reset_session.assert_not_called()
    assert stats.session_resets_topic_switch == 0
    assert stats.reminds_sent == 1


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



def test_apply_project_claude_env_overrides_and_unsets(tmp_path, monkeypatch):
    """LLM 键必须以 .map/.claude-env 为权威，清掉 shell 残留端点/模型。"""
    from cli.agent_client import apply_project_claude_env

    # 无 .map/.claude-env → 环境原样保留
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://legacy.example")
    assert apply_project_claude_env(tmp_path) == {}
    assert os.environ["ANTHROPIC_BASE_URL"] == "https://legacy.example"

    # 有 .claude-env → 文件值覆盖继承值；文件未定义的 LLM 键被 unset；
    # 非 LLM 键（如 ZAI_API_* 残留）不受影响
    env_dir = tmp_path / ".map"
    env_dir.mkdir()
    (env_dir / ".claude-env").write_text(
        "export ANTHROPIC_BASE_URL=http://192.168.20.32:8001\n"
        "export ANTHROPIC_AUTH_TOKEN=empty\n"
        "export ANTHROPIC_MODEL=claude-sonnet-4-6\n"
    )
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL", "glm-5.3[1m]")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "glm-5.3[1m]")
    monkeypatch.setenv("ANTHROPIC_SMALL_FAST_MODEL", "glm-5-turbo")
    monkeypatch.setenv("ZAI_API_LEFTOVER", "secret")

    applied = apply_project_claude_env(tmp_path)
    assert applied["ANTHROPIC_BASE_URL"] == "http://192.168.20.32:8001"
    assert os.environ["ANTHROPIC_BASE_URL"] == "http://192.168.20.32:8001"
    assert os.environ["ANTHROPIC_MODEL"] == "claude-sonnet-4-6"
    assert os.environ["ANTHROPIC_AUTH_TOKEN"] == "empty"
    assert "ANTHROPIC_DEFAULT_SONNET_MODEL" not in os.environ
    assert "ANTHROPIC_SMALL_FAST_MODEL" not in os.environ
    assert os.environ["ZAI_API_LEFTOVER"] == "secret"


def test_simple_waker_run_command_selects_cursor_runtime(monkeypatch, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    captured: dict[str, object] = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        backend = MagicMock()
        backend.connect = AsyncMock()
        backend.disconnect = AsyncMock()
        backend.reset_session = AsyncMock()
        backend.wake_async = AsyncMock()
        return backend

    monkeypatch.setattr("cli.simple_waker.build_wake_backend", fake_build)
    monkeypatch.setattr("cli.simple_waker.SimpleWaker.run_forever", lambda self: None)
    monkeypatch.delenv("MAP_SIMPLE_RUNTIME", raising=False)

    result = CliRunner().invoke(
        simple_waker.APP,
        [
            "--persona",
            "host",
            "--project-root",
            str(tmp_path),
            "--runtime",
            "cursor",
            "--once",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["runtime"] == "cursor"


def test_simple_waker_run_command_runtime_env_fallback(monkeypatch, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    captured: dict[str, object] = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr("cli.simple_waker.build_wake_backend", fake_build)
    monkeypatch.setattr("cli.simple_waker.SimpleWaker.run_forever", lambda self: None)
    monkeypatch.setenv("MAP_SIMPLE_RUNTIME", "cursor")

    result = CliRunner().invoke(
        simple_waker.APP,
        ["--persona", "host", "--project-root", str(tmp_path), "--once", "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert captured["runtime"] == "cursor"


def test_simple_waker_run_command_runtime_flag_wins_over_env(monkeypatch, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    captured: dict[str, object] = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr("cli.simple_waker.build_wake_backend", fake_build)
    monkeypatch.setattr("cli.simple_waker.SimpleWaker.run_forever", lambda self: None)
    monkeypatch.setenv("MAP_SIMPLE_RUNTIME", "cursor")

    result = CliRunner().invoke(
        simple_waker.APP,
        [
            "--persona",
            "host",
            "--project-root",
            str(tmp_path),
            "--runtime",
            "claude",
            "--once",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["runtime"] == "claude"


def test_simple_waker_run_command_rejects_unknown_runtime(monkeypatch, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    monkeypatch.setattr("cli.simple_waker.SimpleWaker.run_forever", lambda self: None)
    result = CliRunner().invoke(
        simple_waker.APP,
        [
            "--persona",
            "host",
            "--project-root",
            str(tmp_path),
            "--runtime",
            "codex",
            "--once",
            "--dry-run",
        ],
    )
    assert result.exit_code == 2
    assert "Unknown waker runtime" in result.output


# ---------------------------------------------------------------------------
# 签名去重（wake_signature + should_send_remind 的 unchanged/max_silence 分支）
# ---------------------------------------------------------------------------


def _handoff_context(author: str, *, notifications: list[dict[str, Any]] | None = None):
    from cli.simple_waker import build_wake_context

    return build_wake_context(
        topic_progress_data={
            "items": [
                {
                    "topic_id": "t1",
                    "topic_title": "Handoff Demo",
                    "discussion_round": "round1",
                    "last_comment_author_name": author,
                    "new_comment_count": 0,
                    "work_items": [{"kind": "unread_change", "priority": "contextual"}],
                }
            ],
            "total": 1,
        },
        todos={},
        notifications=notifications,
    )


def test_wake_signature_unchanged_suppresses_remind() -> None:
    """工作集与上次唤醒一致 → unchanged 跳过；超过 max_silence 兜底放行。"""
    from cli.simple_waker import wake_signature

    context = _handoff_context("participant")
    sig = wake_signature(context)
    now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)

    ok, reason = should_send_remind(
        context,
        now=now,
        last_remind_at=now - timedelta(seconds=300),
        inflight=False,
        min_remind_seconds=30,
        signature=sig,
        last_reminded_signature=sig,
        max_silence_seconds=1800,
    )
    assert ok is False
    assert reason == "unchanged"

    # 僵尸通知场景：同一 group_key 的通知反复出现 → 签名一致 → 持续抑制
    zombie = _handoff_context("participant", notifications=[{"id": "n1", "group_key": "g-zombie"}])
    zombie2 = _handoff_context("participant", notifications=[{"id": "n1", "group_key": "g-zombie"}])
    assert wake_signature(zombie) == wake_signature(zombie2)

    # 超过 max_silence → 兜底唤醒（防签名漏信号导致永久睡死）
    ok, reason = should_send_remind(
        context,
        now=now,
        last_remind_at=now - timedelta(seconds=1900),
        inflight=False,
        min_remind_seconds=30,
        signature=sig,
        last_reminded_signature=sig,
        max_silence_seconds=1800,
    )
    assert ok is True
    assert reason is None


def test_wake_signature_changes_on_real_handoff() -> None:
    """交接信号变化（对方作者进入快照 / 新通知键 / 轮次推进）→ 签名变化 → 唤醒。"""
    from cli.simple_waker import wake_signature

    now = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    sig_waiting = wake_signature(_handoff_context("participant"))

    # 对方又发言一轮（作者不变但轮次推进）
    ctx_round2 = build_wake_context_round("participant", "round2")
    sig_round2 = wake_signature(ctx_round2)
    assert sig_round2 != sig_waiting

    # 新通知键出现
    sig_with_notif = wake_signature(
        _handoff_context("participant", notifications=[{"id": "n9", "group_key": "g-new"}])
    )
    assert sig_with_notif != sig_waiting

    # 签名变化时正常唤醒
    ok, reason = should_send_remind(
        ctx_round2,
        now=now,
        last_remind_at=now - timedelta(seconds=300),
        inflight=False,
        min_remind_seconds=30,
        signature=sig_round2,
        last_reminded_signature=sig_waiting,
        max_silence_seconds=1800,
    )
    assert ok is True
    assert reason is None


def build_wake_context_round(author: str, round_: str):
    from cli.simple_waker import build_wake_context

    return build_wake_context(
        topic_progress_data={
            "items": [
                {
                    "topic_id": "t1",
                    "topic_title": "Handoff Demo",
                    "discussion_round": round_,
                    "last_comment_author_name": author,
                    "new_comment_count": 0,
                    "work_items": [{"kind": "unread_change", "priority": "contextual"}],
                }
            ],
            "total": 1,
        },
        todos={},
    )


# --- 实验 b3ec2e4d I5：busy 状态机回归测试 -------------------------------


class _RecordingHeartbeatClient(FakeMapClient):
    """FakeMapClient + 记录 ``agent_heartbeat`` 调用。"""

    def __init__(self, *, persona: str, todos: dict[str, Any]) -> None:
        super().__init__(persona=persona, todos=todos)
        self.heartbeat_calls: list[Any] = []

    def agent_heartbeat(self, *, busy_since: Any) -> None:
        self.heartbeat_calls.append(busy_since)


def test_touch_busy_writes_state_and_patches_server(tmp_path: Path) -> None:
    """短 fake busy (A1)：_touch_busy 写 state 三字段 + 调 agent_heartbeat。"""
    client = _RecordingHeartbeatClient(persona="host", todos={})
    config = SimpleWakerConfig(
        persona="host",
        project_root=tmp_path,
        state_file=tmp_path / "state.json",
        dry_run=True,
    )
    waker = SimpleWaker(client=client, config=config, backend=MagicMock())
    now = datetime(2026, 8, 31, 11, 0, 0, tzinfo=timezone.utc)
    waker._touch_busy(simple_waker.SimpleWakerStats(), now=now)

    state = load_bridge_state(
        config.state_file,
        bridge_name="simple-waker",
        default_collections=("personas",),
    )["personas"]["host"]
    assert state["session_busy_since"] == now.isoformat()
    assert state["busy_pid"] == os.getpid()
    assert state["busy_started_at"] == now.isoformat()
    assert client.heartbeat_calls == [now]


def test_clear_busy_purges_state_and_server(tmp_path: Path) -> None:
    """短 fake busy (A1)：_clear_busy 清 state 三字段 + 调 heartbeat(None)。"""
    client = _RecordingHeartbeatClient(persona="host", todos={})
    config = SimpleWakerConfig(
        persona="host",
        project_root=tmp_path,
        state_file=tmp_path / "state.json",
        dry_run=True,
    )
    waker = SimpleWaker(client=client, config=config, backend=MagicMock())
    now = datetime(2026, 8, 31, 11, 0, 0, tzinfo=timezone.utc)
    stats = simple_waker.SimpleWakerStats()
    waker._touch_busy(stats, now=now)
    assert client.heartbeat_calls == [now]
    waker._clear_busy(stats)
    assert client.heartbeat_calls == [now, None]
    state = load_bridge_state(
        config.state_file,
        bridge_name="simple-waker",
        default_collections=("personas",),
    )["personas"]["host"]
    assert "busy_pid" not in state
    assert "session_busy_since" not in state


def test_clear_busy_cross_pid_preserves_state(tmp_path: Path) -> None:
    """A8 边界：busy_pid != own_pid 时只清 server,不动 state。"""
    client = _RecordingHeartbeatClient(persona="host", todos={})
    config = SimpleWakerConfig(
        persona="host",
        project_root=tmp_path,
        state_file=tmp_path / "state.json",
        dry_run=True,
    )
    waker = SimpleWaker(client=client, config=config, backend=MagicMock())
    # 写入 busy_pid=99999(死 PID)跨进程场景
    state = {
        "schema_version": 1,
        "personas": {
            "host": {
                "busy_pid": 99999,
                "busy_started_at": "2026-08-31T11:00:00+00:00",
                "session_busy_since": "2026-08-31T11:00:00+00:00",
            }
        },
    }
    save_bridge_state(config.state_file, state)
    waker.state = load_bridge_state(
        config.state_file,
        bridge_name="simple-waker",
        default_collections=("personas",),
    )

    waker._clear_busy(simple_waker.SimpleWakerStats())
    # 跨 PID 不应清 state
    state_after = load_bridge_state(
        config.state_file,
        bridge_name="simple-waker",
        default_collections=("personas",),
    )["personas"]["host"]
    assert state_after.get("busy_pid") == 99999
    # 但应清 server
    assert client.heartbeat_calls == [None]


def test_check_busy_crash_recovery_clears_dead_pid(tmp_path: Path) -> None:
    """A2：dead busy_pid 触发 crash recovery,清 state + server。"""
    client = _RecordingHeartbeatClient(persona="host", todos={})
    config = SimpleWakerConfig(
        persona="host",
        project_root=tmp_path,
        state_file=tmp_path / "state.json",
        dry_run=True,
    )
    state = {
        "schema_version": 1,
        "personas": {
            "host": {
                "busy_pid": 99999,
                "busy_started_at": "2026-08-31T11:00:00+00:00",
                "session_busy_since": "2026-08-31T11:00:00+00:00",
            }
        },
    }
    save_bridge_state(config.state_file, state)

    SimpleWaker(client=client, config=config, backend=MagicMock())
    state_after = load_bridge_state(
        config.state_file,
        bridge_name="simple-waker",
        default_collections=("personas",),
    )["personas"]["host"]
    assert "busy_pid" not in state_after
    assert client.heartbeat_calls == [None]


def test_run_once_calls_touch_and_clear_around_wake(tmp_path: Path) -> None:
    """短 fake busy (A1)：run_once 在 wake_async 前 touch / finally clear。

    不用 ``dry_run=True`` 是因为 dry_run 路径在 ``should_send_remind`` 通过
    后会提前 return,不进入 wake_async 也不会触发 busy touch/clear——直接
    用真实 backend.wake_async mock 走完完整流程。
    """

    async def fake_wake_async(**kwargs):  # noqa: ARG001
        return None

    backend = MagicMock()
    backend.wake_async = fake_wake_async
    client = _RecordingHeartbeatClient(
        persona="host",
        todos={"pending_topic_replies": [{"comment_id": "c1", "topic_id": "t1"}]},
    )
    config = SimpleWakerConfig(
        persona="host",
        project_root=tmp_path,
        state_file=tmp_path / "state.json",
    )
    waker = SimpleWaker(client=client, config=config, backend=backend)
    waker.run_once()

    # touch (datetime) + clear (None) 各一次
    assert len(client.heartbeat_calls) == 2
    assert isinstance(client.heartbeat_calls[0], datetime)
    assert client.heartbeat_calls[1] is None
