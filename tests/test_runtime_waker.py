import json
import importlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MapCommandClient = importlib.import_module("cli.map_command_client").MapCommandClient
runtime_waker = importlib.import_module("cli.runtime_waker")
RuntimeWaker = runtime_waker.RuntimeWaker
RuntimeWakerConfig = runtime_waker.RuntimeWakerConfig
WakeResult = runtime_waker.WakeResult
CodexSdkWakeBackend = runtime_waker.CodexSdkWakeBackend
CursorSdkWakeBackend = runtime_waker.CursorSdkWakeBackend
build_wake_prompt = runtime_waker.build_wake_prompt
create_backend = runtime_waker.create_backend
discover_wake_events = runtime_waker.discover_wake_events
should_reset_session_for_context = runtime_waker.should_reset_session_for_context
sync_runtime_skills = runtime_waker.sync_runtime_skills
wake_context_key = runtime_waker.wake_context_key
wake_skill_paths = runtime_waker.wake_skill_paths


def _host_topic_todos(
    topic_id: str = "topic-1",
    comment_id: str = "comment-1",
    *,
    author_id: str = "participant-1",
    comment_count: int = 1,
    **topic_fields: Any,
) -> dict[str, Any]:
    del comment_count
    return _pending_topic_reply_todos(
        topic_id=topic_id,
        comment_id=comment_id,
        author_agent_id=author_id,
        **topic_fields,
    )


def _pending_topic_reply_todos(
    topic_id: str = "topic-1",
    comment_id: str = "comment-1",
    **fields: Any,
) -> dict[str, Any]:
    row = {
        "topic_id": topic_id,
        "comment_id": comment_id,
        "topic_title": "T",
        "author_agent_id": "participant-1",
        "author_name": "participant",
        "excerpt": "hello",
        "created_at": "2026-07-02T10:00:00+00:00",
        **fields,
    }
    return {"pending_topic_replies": [row]}


class FakeMapClient(MapCommandClient):
    def __init__(
        self,
        *,
        persona: str,
        todos: dict[str, Any],
        open_topics: list[dict[str, Any]] | None = None,
        duplicate_fingerprints: set[str] | None = None,
    ) -> None:
        self.persona = persona
        self._todos = todos
        self._open_topics = open_topics or []
        self._duplicate_fingerprints: set[str] = set(duplicate_fingerprints or set())
        self.record_calls: list[dict[str, Any]] = []
        self.duplicate_on_call: dict[str, int] = {}

    def whoami(self) -> dict[str, Any]:
        return {"id": f"{self.persona}-agent", "name": self.persona}

    def todos(self) -> dict[str, Any]:
        return self._todos

    def notifications_unread(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return []

    def topic_list_open(self) -> list[dict[str, Any]]:
        return self._open_topics

    def inbound_event_record(
        self,
        *,
        event_id: str,
        fingerprint: str,
        event_type: str,
        source: str = "polling",
    ) -> bool:
        self.record_calls.append(
            {
                "event_id": event_id,
                "fingerprint": fingerprint,
                "event_type": event_type,
                "source": source,
            }
        )
        # Per-fingerprint override: return False (server says duplicate).
        if fingerprint in self.duplicate_fingerprints:
            return False
        # Optional: "fail on the Nth call to this fingerprint" — lets a test
        # express "first call OK, subsequent calls 409".
        limit = self.duplicate_on_call.get(fingerprint)
        if limit is not None:
            seen = sum(
                1
                for c in self.record_calls[:-1]
                if c["fingerprint"] == fingerprint
            )
            if seen >= limit:
                return False
        return True

    @property
    def duplicate_fingerprints(self) -> set[str]:
        return self._duplicate_fingerprints


class FakeWakeBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def wake(self, *, persona: str, prompt: str, session_id: str | None) -> WakeResult:
        self.calls.append({"persona": persona, "prompt": prompt, "session_id": session_id})
        return WakeResult(session_id=f"session-{persona}", response_text="done")


def test_discover_host_events_from_todos():
    events = discover_wake_events(
        "host",
        {
            "pending_topic_replies": [
                {
                    "topic_id": "topic-2",
                    "comment_id": "comment-new",
                    "topic_title": "Open",
                    "author_agent_id": "participant-1",
                    "author_name": "multi-agents-platform-participant",
                    "excerpt": "new comment",
                    "created_at": "2026-07-02T10:00:00+00:00",
                }
            ],
            "my_open_experiments": [
                {"id": "exp-1", "title": "Exp", "phase": "review", "current_plan_version": 2}
            ],
        },
    )

    assert [event.kind for event in events] == [
        "pending_topic_replies",
        "my_open_experiments",
    ]
    assert events[0].fingerprint == "host:pending_topic_replies:comment-new"
    assert events[1].fingerprint == "host:my_open_experiments:exp-1"


def test_host_events_pending_advance_rounds():
    events = discover_wake_events(
        "host",
        {
            "pending_advance_rounds": [
                {
                    "topic_id": "topic-advance",
                    "topic_title": "Ready topic",
                    "discussion_round": "round1",
                    "advance_round_pending_since": "2026-07-02T10:40:42+00:00",
                    "updated_at": "2026-07-02T10:40:42+00:00",
                }
            ],
        },
    )
    assert [event.kind for event in events] == ["pending_advance_rounds"]
    assert events[0].fingerprint.startswith("host:pending_advance_rounds:topic-advance:")


def test_my_open_topics_always_wake():
    events = discover_wake_events(
        "host",
        {
            "my_open_topics": [
                {
                    "id": "topic-1",
                    "title": "T",
                    "comment_count": 2,
                    "last_comment_id": "c2",
                    "last_comment_author_agent_id": "host-agent-1",
                }
            ],
        },
    )
    assert len(events) == 1
    assert events[0].kind == "my_open_topics"
    assert events[0].fingerprint == "host:my_open_topics:topic-1"


def test_my_open_experiments_fingerprint_includes_open_unreasonable_count():
    events = discover_wake_events(
        "host",
        {
            "my_open_experiments": [
                {
                    "id": "exp-1",
                    "title": "Exp",
                    "phase": "review",
                    "current_plan_version": 1,
                    "open_unreasonable_count": 9,
                }
            ],
        },
    )
    assert len(events) == 1
    assert events[0].fingerprint == "host:my_open_experiments:exp-1"


def test_my_open_experiments_fingerprint_zero_open_count_is_explicit():
    events = discover_wake_events(
        "host",
        {
            "my_open_experiments": [
                {
                    "id": "exp-1",
                    "title": "Exp",
                    "phase": "review",
                    "current_plan_version": 1,
                    "open_unreasonable_count": 0,
                }
            ],
        },
    )
    assert events[0].fingerprint == "host:my_open_experiments:exp-1"


def test_discover_reviewer_and_participant_events():
    reviewer_events = discover_wake_events(
        "reviewer",
        {
            "pending_reviews": [{"id": "exp-1", "title": "E1", "current_plan_version": 3}],
            "pending_result_reviews": [{"id": "exp-2", "title": "E2", "phase": "result_review", "updated_at": "t1"}],
            "pending_replies": [{"item_id": "item-1", "status": "addressed", "experiment_id": "exp-1"}],
            "mentions": [{"id": "mention-9", "topic_id": "topic-2", "source_id": "comment-9", "excerpt": "hi"}],
        },
    )
    participant_events = discover_wake_events(
        "participant",
        {"mentions": [{"id": "mention-1", "topic_id": "topic-1", "source_id": "comment-1", "excerpt": "hi"}]},
    )

    assert [event.kind for event in reviewer_events] == [
        "mentions",
        "pending_reviews",
        "pending_result_reviews",
        "pending_replies",
    ]
    assert reviewer_events[0].fingerprint == "reviewer:mentions:mention-9"
    assert reviewer_events[1].fingerprint == "reviewer:pending_reviews:exp-1"
    assert reviewer_events[2].fingerprint == "reviewer:pending_result_reviews:exp-2"
    assert reviewer_events[3].fingerprint == "reviewer:pending_replies:item-1"
    assert participant_events[0].fingerprint == "participant:mentions:mention-1"


def test_discover_pending_round_acks_events():
    participant_events = discover_wake_events(
        "participant",
        {
            "pending_round_acks": [
                {
                    "topic_id": "topic-ack",
                    "topic_title": "Ack topic",
                    "summary_comment_id": "summary-1",
                    "advance_round_pending_since": "2026-07-01T12:00:00+00:00",
                }
            ],
        },
    )
    reviewer_events = discover_wake_events(
        "reviewer",
        {
            "pending_round_acks": [
                {
                    "topic_id": "topic-ack",
                    "topic_title": "Ack topic",
                    "summary_comment_id": "summary-1",
                    "advance_round_pending_since": "2026-07-01T12:00:00+00:00",
                }
            ],
        },
    )

    assert [event.kind for event in participant_events] == ["pending_round_acks"]
    assert participant_events[0].fingerprint.startswith("participant:pending_round_acks:topic-ack:")
    assert reviewer_events[0].persona == "reviewer"
    prompt = build_wake_prompt(participant_events[0], project_root=Path.cwd())
    assert "topic advance-round" in prompt
    assert "--ack accept" in prompt


def test_runtime_waker_wakes_once_and_persists_session(tmp_path):
    state_file = tmp_path / "runtime-waker-state.json"
    backend = FakeWakeBackend()
    client = FakeMapClient(
        persona="host",
        todos=_host_topic_todos(),
    )
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="host",
            once=True,
            state_file=state_file,
            project_root=Path.cwd(),
            cooldown_seconds=60,
            backend="codex",
        ),
        backend=backend,
    )

    stats = worker.run_once()

    assert stats.wakes_sent == 1
    assert backend.calls[0]["session_id"] is None
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["personas"]["host"]["runtime_session_id"] == "session-host"
    assert state["personas"]["host"]["last_wake_object_id"] == "topic-1"
    assert state["personas"]["host"]["events"]["host:pending_topic_replies:comment-1"]["status"] == "woken"


def test_runtime_waker_skips_already_woken_event(tmp_path):
    # Under the self-heal TTL, a woken event is skipped only while woken_at is
    # still within woken_cooldown_seconds. Use a freshly-stamped woken_at so the
    # default 1800s TTL has not elapsed.
    recent = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    state_file = tmp_path / "runtime-waker-state.json"
    state_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "personas": {
                    "host": {
                        "runtime_session_id": "existing-session",
                        "events": {
                            "host:pending_topic_replies:comment-1": {
                                "status": "woken",
                                "woken_at": recent,
                                "last_attempt_at": recent,
                            }
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    backend = FakeWakeBackend()
    client = FakeMapClient(
        persona="host",
        todos=_host_topic_todos(),
    )
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(persona="host", state_file=state_file, project_root=Path.cwd()),
        backend=backend,
    )

    stats = worker.run_once()

    assert stats.wake_skips == 1
    assert backend.calls == []


def test_woken_event_self_heals_after_ttl(tmp_path):
    # A woken event whose woken_at is older than woken_cooldown_seconds must be
    # reconsidered instead of skipped forever — this is the self-heal that
    # breaks deadlocks when an agent woke but took no action.
    stale = (datetime.now(UTC) - timedelta(seconds=3600)).isoformat()
    state_file = tmp_path / "runtime-waker-state.json"
    state_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "personas": {
                    "host": {
                        "events": {
                            "host:pending_topic_replies:comment-1": {
                                "status": "woken",
                                "woken_at": stale,
                                "last_attempt_at": stale,
                            }
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    backend = FakeWakeBackend()
    client = FakeMapClient(
        persona="host",
        todos=_host_topic_todos(),
    )
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="host",
            state_file=state_file,
            project_root=Path.cwd(),
            woken_cooldown_seconds=1800,
        ),
        backend=backend,
    )

    stats = worker.run_once()

    assert stats.wakes_sent == 1
    # The wake must refresh woken_at so the TTL restarts from this attempt.
    state = json.loads(state_file.read_text(encoding="utf-8"))
    record = state["personas"]["host"]["events"]["host:pending_topic_replies:comment-1"]
    assert record["status"] == "woken"
    assert record["woken_at"] > stale


def test_woken_fallback_to_last_attempt_at(tmp_path):
    # State files written before woken_at existed have no woken_at stamp; the
    # TTL must fall back to last_attempt_at so legacy records also self-heal.
    stale = (datetime.now(UTC) - timedelta(seconds=3600)).isoformat()
    state_file = tmp_path / "runtime-waker-state.json"
    state_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "personas": {
                    "host": {
                        "events": {
                            "host:pending_topic_replies:comment-1": {
                                "status": "woken",
                                "last_attempt_at": stale,
                            }
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    backend = FakeWakeBackend()
    client = FakeMapClient(
        persona="host",
        todos=_host_topic_todos(),
    )
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="host",
            state_file=state_file,
            project_root=Path.cwd(),
            woken_cooldown_seconds=1800,
        ),
        backend=backend,
    )

    stats = worker.run_once()

    assert stats.wakes_sent == 1


def test_runtime_waker_uses_persisted_session_when_forced(tmp_path):
    state_file = tmp_path / "runtime-waker-state.json"
    state_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "personas": {
                    "host": {
                        "runtime_session_id": "existing-session",
                        "claude_session_id": "existing-session",
                        "events": {},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    backend = FakeWakeBackend()
    client = FakeMapClient(
        persona="host",
        todos=_host_topic_todos(),
    )
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(persona="host", state_file=state_file, project_root=Path.cwd(), force=True),
        backend=backend,
    )

    stats = worker.run_once()

    assert stats.wakes_sent == 1
    assert backend.calls[0]["session_id"] == "existing-session"


def test_wake_context_key_groups_events_by_map_object():
    topic_event = discover_wake_events(
        "host",
        _host_topic_todos("topic-a", "comment-1"),
    )[0]
    experiment_event = discover_wake_events(
        "reviewer",
        {"pending_reviews": [{"id": "experiment-a", "current_plan_version": 1}]},
    )[0]
    addressed_item_event = discover_wake_events(
        "reviewer",
        {
            "pending_replies": [
                {"item_id": "item-a", "status": "addressed", "experiment_id": "experiment-a"}
            ]
        },
    )[0]

    assert wake_context_key(topic_event) == "topic:topic-a"
    assert wake_context_key(experiment_event) == "experiment:experiment-a"
    assert wake_context_key(addressed_item_event) == "experiment:experiment-a"


def test_should_reset_session_for_context():
    assert not should_reset_session_for_context(last_wake_context_key=None, wake_context_key="topic:topic-a")
    assert not should_reset_session_for_context(
        last_wake_context_key="topic:topic-a", wake_context_key="topic:topic-a"
    )
    assert should_reset_session_for_context(
        last_wake_context_key="topic:topic-a", wake_context_key="topic:topic-b"
    )


def test_runtime_waker_clears_codex_session_when_wake_object_changes(tmp_path):
    state_file = tmp_path / "runtime-waker-state.json"
    state_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "personas": {
                    "host": {
                        "claude_session_id": "session-old",
                        "runtime_session_id": "session-old",
                        "last_wake_context_key": "topic:topic-a",
                        "events": {},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    backend = FakeWakeBackend()
    client = FakeMapClient(
        persona="host",
        todos=_host_topic_todos("topic-b", "comment-1"),
    )
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="host",
            once=True,
            state_file=state_file,
            project_root=Path.cwd(),
            backend="codex",
        ),
        backend=backend,
    )

    worker.run_once()

    assert backend.calls[0]["session_id"] is None
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["personas"]["host"]["last_wake_context_key"] == "topic:topic-b"
    assert state["personas"]["host"]["last_wake_object_id"] == "topic-b"
    assert state["personas"]["host"]["runtime_session_id"] == "session-host"


def test_runtime_waker_keeps_codex_session_for_same_wake_object(tmp_path):
    state_file = tmp_path / "runtime-waker-state.json"
    state_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "personas": {
                    "host": {
                        "claude_session_id": "session-old",
                        "runtime_session_id": "session-old",
                        "last_wake_context_key": "topic:topic-a",
                        "events": {},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    backend = FakeWakeBackend()
    client = FakeMapClient(
        persona="host",
        todos=_host_topic_todos("topic-a", "comment-2"),
    )
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="host",
            once=True,
            state_file=state_file,
            project_root=Path.cwd(),
            backend="codex",
            force=True,
        ),
        backend=backend,
    )

    worker.run_once()

    assert backend.calls[0]["session_id"] == "session-old"


def test_runtime_waker_keeps_session_for_same_experiment_review_context(tmp_path):
    state_file = tmp_path / "runtime-waker-state.json"
    state_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "personas": {
                    "reviewer": {
                        "claude_session_id": "session-old",
                        "runtime_session_id": "session-old",
                        "last_wake_context_key": "experiment:experiment-a",
                        "events": {},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    backend = FakeWakeBackend()
    client = FakeMapClient(
        persona="reviewer",
        todos={
            "pending_replies": [
                {"item_id": "item-a", "status": "addressed", "experiment_id": "experiment-a"}
            ]
        },
    )
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="reviewer",
            once=True,
            state_file=state_file,
            project_root=Path.cwd(),
            backend="codex",
        ),
        backend=backend,
    )

    worker.run_once()

    assert backend.calls[0]["session_id"] == "session-old"


def test_runtime_waker_resets_claude_backend_when_wake_object_changes(tmp_path):
    import asyncio

    class FakeAgentClient:
        def __init__(self, state: dict[str, Any]) -> None:
            self.state = state
            self.resume_ids: list[str | None] = []
            self.disconnected = 0

        async def connect(self) -> None:
            self.resume_ids.append(self.state.get("claude_session_id"))

        async def disconnect(self) -> None:
            self.disconnected += 1

        async def wake_up(self, prompt: str, **_kwargs: Any) -> str:
            del prompt
            self.state["claude_session_id"] = "session-new"
            return "ok"

    state_file = tmp_path / "runtime-waker-state.json"
    state_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "personas": {
                    "host": {
                        "claude_session_id": "session-old",
                        "runtime_session_id": "session-old",
                        "last_wake_context_key": "topic:topic-a",
                        "events": {},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    holder: dict[str, RuntimeWaker] = {}
    backend = runtime_waker.PersonaAgentWakeBackend(
        project_root=Path.cwd(),
        persona="host",
        get_agent_state=lambda: holder["worker"]._persona_state("host"),
        save_state_fn=lambda: None,
    )
    worker = RuntimeWaker(
        client=FakeMapClient(
            persona="host",
            todos=_host_topic_todos("topic-b", "comment-1"),
        ),
        config=RuntimeWakerConfig(
            persona="host",
            once=True,
            state_file=state_file,
            project_root=Path.cwd(),
        ),
        backend=backend,
    )
    holder["worker"] = worker
    backend._agent_client = FakeAgentClient(worker._persona_state("host"))  # noqa: SLF001

    asyncio.run(worker._run_once_async())

    fake_client = backend._agent_client
    assert fake_client.disconnected >= 1
    assert fake_client.resume_ids[-1] is None
    host_state = worker._persona_state("host")
    assert host_state["last_wake_context_key"] == "topic:topic-b"
    assert host_state["last_wake_object_id"] == "topic-b"
    assert host_state["claude_session_id"] == "session-new"


def test_participant_waker_scans_open_topics_by_default(tmp_path):
    backend = FakeWakeBackend()
    client = FakeMapClient(
        persona="participant",
        todos={"my_open_topics": [{"id": "topic-open", "title": "Open"}]},
    )
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="participant",
            state_file=tmp_path / "state.json",
            project_root=Path.cwd(),
        ),
        backend=backend,
    )

    stats = worker.run_once()

    assert stats.events_seen == 1
    assert stats.wakes_sent == 1
    assert "MAP wake · my_open_topics · topic-open" in backend.calls[0]["prompt"]


def test_participant_empty_todos_has_no_events():
    events = discover_wake_events("participant", {})
    assert events == []


def test_participant_my_open_topics_wake():
    events = discover_wake_events(
        "participant",
        {
            "my_open_topics": [
                {
                    "id": "topic-1",
                    "title": "T",
                    "comment_count": 1,
                    "last_comment_id": "comment-1",
                    "last_comment_author_agent_id": "host-agent",
                }
            ]
        },
    )
    assert len(events) == 1
    assert events[0].kind == "my_open_topics"
    assert events[0].fingerprint == "participant:my_open_topics:topic-1"


def test_participant_waker_with_my_open_topics(tmp_path):
    backend = FakeWakeBackend()
    client = FakeMapClient(
        persona="participant",
        todos={"my_open_topics": [{"id": "topic-open", "title": "Open"}]},
    )
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="participant",
            state_file=tmp_path / "state.json",
            project_root=Path.cwd(),
        ),
        backend=backend,
    )

    stats = worker.run_once()

    assert stats.events_seen == 1
    assert stats.wakes_sent == 1
    assert "MAP wake · my_open_topics · topic-open" in backend.calls[0]["prompt"]


def test_build_wake_prompt_is_short_and_cli_oriented():
    event = discover_wake_events(
        "host",
        {
            "pending_topic_replies": [
                {
                    "topic_id": "topic-1",
                    "comment_id": "comment-1",
                    "topic_title": "T",
                    "author_agent_id": "participant-1",
                    "author_name": "multi-agents-platform-participant",
                    "excerpt": "需要 host 回复",
                    "created_at": "2026-07-02T10:00:00+00:00",
                }
            ],
        },
    )[0]

    prompt = build_wake_prompt(event, project_root=Path("/tmp/multi_agents_platform"))

    assert prompt.startswith("MAP wake · pending_topic_replies · topic-1")
    assert "话题待回复" in prompt
    assert "latest_by=multi-agents-platform-participant" in prompt
    assert "excerpt=需要 host 回复" in prompt
    assert "map --persona host topic show --id topic-1" in prompt
    assert "不要使用 MCP" not in prompt
    assert "```json" not in prompt
    assert len(prompt) < 500


def test_wake_skill_paths_for_host(tmp_path):
    for name in ("map-runtime-waker", "map-project-collab", "topic-host", "experiment-host"):
        skill_dir = tmp_path / ".cursor" / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"name: {name}\n", encoding="utf-8")

    names = [path.parent.name for path in wake_skill_paths(tmp_path, "host")]
    assert names == ["map-runtime-waker", "map-project-collab", "topic-host", "experiment-host"]


def test_wake_skill_paths_skips_missing_files(tmp_path):
    waker = tmp_path / ".cursor" / "skills" / "map-runtime-waker" / "SKILL.md"
    waker.parent.mkdir(parents=True)
    waker.write_text("name: map-runtime-waker\n", encoding="utf-8")

    names = [path.parent.name for path in wake_skill_paths(tmp_path, "reviewer")]
    assert names == ["map-runtime-waker"]


def test_sync_runtime_skills_copies_skill_dirs(tmp_path):
    project_root = tmp_path / "project"
    source = project_root / ".cursor" / "skills" / "example-skill"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: example-skill\ndescription: test\n---\n", encoding="utf-8")
    stale = tmp_path / "home" / ".claude" / "skills" / "stale-skill"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("stale", encoding="utf-8")

    sync_runtime_skills(project_root=project_root, runtime_home=tmp_path / "home")

    target = tmp_path / "home" / ".claude" / "skills" / "example-skill" / "SKILL.md"
    assert target.read_text(encoding="utf-8").startswith("---")
    assert not stale.exists()


def test_create_backend_supports_codex(tmp_path):
    backend = create_backend(RuntimeWakerConfig(backend="codex", project_root=tmp_path, codex_bin="/bin/codex"))

    assert isinstance(backend, CodexSdkWakeBackend)
    assert backend.codex_bin == "/bin/codex"


def test_create_backend_supports_cursor(tmp_path):
    backend = create_backend(RuntimeWakerConfig(backend="cursor", project_root=tmp_path, model="composer-2.5"))

    assert isinstance(backend, CursorSdkWakeBackend)
    assert backend.model == "composer-2.5"


def test_default_claude_backend_uses_persona_agent_wake_backend(tmp_path):
    client = FakeMapClient(persona="host", todos={})
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(persona="host", project_root=tmp_path, state_file=tmp_path / "s.json"),
    )

    assert isinstance(worker.backend, runtime_waker.PersonaAgentWakeBackend)


def test_codex_backend_starts_and_resumes_threads(monkeypatch, tmp_path):
    calls: list[tuple[str, Any]] = []

    class FakeApprovalMode:
        deny_all = "deny_all"

    class FakeSandbox:
        workspace_write = "workspace_write"

    class FakeTextInput:
        def __init__(self, text: str) -> None:
            self.text = text

    class FakeSkillInput:
        def __init__(self, *, name: str, path: str) -> None:
            self.name = name
            self.path = path

    class FakeCodexConfig:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(("config", kwargs))

    class FakeTurnResult:
        final_response = "handled"

    class FakeThread:
        def __init__(self, thread_id: str) -> None:
            self.id = thread_id

        def run(self, input_items: list[Any], **kwargs: Any) -> FakeTurnResult:
            calls.append(("run", {"input_items": input_items, "kwargs": kwargs}))
            return FakeTurnResult()

    class FakeCodex:
        def __init__(self, *, config: FakeCodexConfig) -> None:
            calls.append(("codex", config))

        def __enter__(self) -> "FakeCodex":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def thread_start(self, **kwargs: Any) -> FakeThread:
            calls.append(("thread_start", kwargs))
            return FakeThread("new-thread")

        def thread_resume(self, thread_id: str, **kwargs: Any) -> FakeThread:
            calls.append(("thread_resume", {"thread_id": thread_id, "kwargs": kwargs}))
            return FakeThread(thread_id)

    fake_module = type(sys)("openai_codex")
    fake_module.ApprovalMode = FakeApprovalMode
    fake_module.Codex = FakeCodex
    fake_module.CodexConfig = FakeCodexConfig
    fake_module.Sandbox = FakeSandbox
    fake_module.SkillInput = FakeSkillInput
    fake_module.TextInput = FakeTextInput
    monkeypatch.setitem(sys.modules, "openai_codex", fake_module)

    skill = tmp_path / ".cursor" / "skills" / "map-runtime-waker"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: map-runtime-waker\ndescription: test\n---\n", encoding="utf-8")
    for name in ("map-project-collab", "topic-host", "experiment-host"):
        extra = tmp_path / ".cursor" / "skills" / name
        extra.mkdir(parents=True, exist_ok=True)
        (extra / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test\n---\n", encoding="utf-8")

    backend = CodexSdkWakeBackend(
        project_root=tmp_path,
        runtime_home=tmp_path / "codex-home",
        model="gpt-5.4",
        codex_bin="/usr/bin/codex",
    )

    first = backend.wake(persona="host", prompt="wake now", session_id=None)
    second = backend.wake(persona="host", prompt="wake again", session_id=first.session_id)

    assert first.session_id == "new-thread"
    assert first.response_text == "handled"
    assert second.session_id == "new-thread"
    assert ("thread_resume", {"thread_id": "new-thread", "kwargs": calls[-2][1]["kwargs"]}) in calls

    config_call = next(payload for name, payload in calls if name == "config")
    assert config_call["codex_bin"] == "/usr/bin/codex"
    assert config_call["env"]["CODEX_HOME"] == str(tmp_path / "codex-home")
    run_call = next(payload for name, payload in calls if name == "run")
    skill_names = [item.name for item in run_call["input_items"] if isinstance(item, FakeSkillInput)]
    assert skill_names == ["map-runtime-waker", "map-project-collab", "topic-host", "experiment-host"]
    assert isinstance(run_call["input_items"][-1], FakeTextInput)


def test_cursor_backend_creates_and_resumes_agents(monkeypatch, tmp_path):
    calls: list[tuple[str, Any]] = []

    class FakeRunResult:
        status = "finished"
        result = "handled"
        id = "run-1"

    class FakeRun:
        def wait(self) -> FakeRunResult:
            calls.append(("wait", {}))
            return FakeRunResult()

    class FakeAgent:
        def __init__(self, agent_id: str) -> None:
            self.agent_id = agent_id

        def send(self, prompt: str) -> FakeRun:
            calls.append(("send", {"prompt": prompt}))
            return FakeRun()

        def close(self) -> None:
            calls.append(("close", {}))

    class FakeAgentContext:
        def __init__(self, agent_id: str) -> None:
            self._agent = FakeAgent(agent_id)

        def __enter__(self) -> FakeAgent:
            return self._agent

        def __exit__(self, *args: Any) -> None:
            return None

    class FakeLocalAgentOptions:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(("local", kwargs))

    class FakeAgentOptions:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(("options", kwargs))

    class FakeAgentClass:
        @staticmethod
        def create(options: FakeAgentOptions) -> FakeAgentContext:
            calls.append(("create", options))
            return FakeAgentContext("agent-new")

        @staticmethod
        def resume(agent_id: str, options: FakeAgentOptions) -> FakeAgentContext:
            calls.append(("resume", {"agent_id": agent_id, "options": options}))
            return FakeAgentContext(agent_id)

    fake_module = type(sys)("cursor_sdk")
    fake_module.Agent = FakeAgentClass
    fake_module.AgentOptions = FakeAgentOptions
    fake_module.CursorAgentError = type("CursorAgentError", (Exception,), {})
    fake_module.LocalAgentOptions = FakeLocalAgentOptions
    monkeypatch.setitem(sys.modules, "cursor_sdk", fake_module)
    monkeypatch.setenv("CURSOR_API_KEY", "cursor_test_key")

    backend = CursorSdkWakeBackend(project_root=tmp_path, model="composer-2.5")

    first = backend.wake(persona="host", prompt="wake now", session_id=None)
    second = backend.wake(persona="host", prompt="wake again", session_id=first.session_id)

    assert first.session_id == "agent-new"
    assert first.response_text == "handled"
    assert second.session_id == "agent-new"
    resume_calls = [payload for name, payload in calls if name == "resume"]
    assert len(resume_calls) == 1
    assert resume_calls[0]["agent_id"] == "agent-new"
    options_call = next(payload for name, payload in calls if name == "options")
    assert options_call["api_key"] == "cursor_test_key"
    assert options_call["model"] == "composer-2.5"
    local_call = next(payload for name, payload in calls if name == "local")
    assert local_call["cwd"] == str(tmp_path)
    assert local_call["setting_sources"] == ["project"]
    send_calls = [payload for name, payload in calls if name == "send"]
    assert send_calls[0]["prompt"] == "wake now"
    assert send_calls[1]["prompt"] == "wake again"


def test_cursor_backend_requires_api_key(monkeypatch, tmp_path):
    monkeypatch.setattr(CursorSdkWakeBackend, "_resolve_api_key", lambda self: None)
    backend = CursorSdkWakeBackend(project_root=tmp_path)

    try:
        backend.wake(persona="host", prompt="wake", session_id=None)
    except Exception as exc:
        assert "CURSOR_API_KEY" in str(exc)
    else:
        raise AssertionError("expected WorkerError for missing CURSOR_API_KEY")


def test_round_robin_by_kind_prevents_kind_starvation():
    """With 3 pending_topic_replies + 2 my_open_experiments at limit=3, round-robin
    must give my_open_experiments a slot instead of letting topics starve it."""
    topics = [
        runtime_waker.WakeEvent(
            persona="host", kind="pending_topic_replies", object_id=f"t{i}", fingerprint=f"t{i}"
        )
        for i in range(3)
    ]
    experiments = [
        runtime_waker.WakeEvent(
            persona="host", kind="my_open_experiments", object_id=f"e{i}", fingerprint=f"e{i}"
        )
        for i in range(2)
    ]
    selected = runtime_waker._round_robin_by_kind(topics + experiments, limit=3)
    assert len(selected) == 3
    kinds = [e.kind for e in selected]
    # first pass takes one of each kind, then a second topic
    assert kinds == ["pending_topic_replies", "my_open_experiments", "pending_topic_replies"]


def test_round_robin_by_kind_limit_zero():
    assert runtime_waker._round_robin_by_kind(
        [
            runtime_waker.WakeEvent(
                persona="host", kind="pending_topic_replies", object_id="t", fingerprint="t"
            )
        ],
        limit=0,
    ) == []


def test_host_cycle_does_not_starve_experiments(tmp_path):
    """Open topics without unseen comments must not monopolize the wake budget.

    Regression for the deadlock where host kept waking only topic events
    and never reached my_open_experiments, so experiments stuck in review never
    got approved.
    """
    state_file = tmp_path / "state.json"
    backend = FakeWakeBackend()
    client = FakeMapClient(
        persona="host",
        todos={
            "my_open_topics": [
                {"id": f"topic-{i}", "title": f"T{i}", "discussion_round": "round1"}
                for i in range(3)
            ],
            "my_open_experiments": [
                {"id": f"exp-{i}", "title": f"E{i}", "phase": "review", "current_plan_version": 1}
                for i in range(2)
            ],
        },
    )
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="host",
            once=True,
            state_file=state_file,
            project_root=Path.cwd(),
            max_wakes_per_cycle=3,
            backend="codex",
        ),
        backend=backend,
    )

    worker.run_once()

    woken_prompts = [c["prompt"] for c in backend.calls]
    assert any("my_open_experiments" in p for p in woken_prompts), (
        "my_open_experiments starved by hosted topics"
    )
    assert not any("pending_topic_replies" in p for p in woken_prompts)


def _stale_event_records(count: int, stale: str, *, kind: str, object_id: str, prefix: str) -> dict[str, Any]:
    """Build N orphaned dedup entries (derived-fingerprint variants) aged to ``stale``."""
    return {
        f"{prefix}-{i}": {"status": "woken", "last_attempt_at": stale, "kind": kind, "object_id": object_id}
        for i in range(count)
    }


def test_prune_monotonically_shrinks_dead_entries(tmp_path):
    """A1: orphaned dead entries (derived-fingerprint variants + resolved objects)
    are pruned and the count only ever decreases across cycles.

    The fingerprint encodes derived fields, so once the underlying object moves the
    old key is never matched again. With empty todos nothing is rediscovered, so the
    sweep is the only thing touching these entries. Prune runs every cycle, so all
    stale orphans clear in one pass; subsequent cycles must not regrow the set.
    """
    state_file = tmp_path / "state.json"
    stale = (datetime.now(UTC) - timedelta(seconds=3600)).isoformat()
    dead_events: dict[str, Any] = {}
    # 120 pending_topic_reply orphans: same topic, different comment_id.
    dead_events.update(
        _stale_event_records(
            120, stale, kind="pending_topic_reply", object_id="topic-1",
            prefix="host:pending_topic_reply:topic-1:comment",
        )
    )
    # 80 my_open_experiments orphans: same experiment, different plan version.
    dead_events.update(
        _stale_event_records(
            80, stale, kind="my_open_experiments", object_id="exp-1",
            prefix="host:my_open_experiments:exp-1-old",
        )
    )
    # A truly resolved object: experiment gone from todos, fingerprint frozen forever.
    dead_events["host:my_open_experiments:exp-resolved"] = {
        "status": "woken", "last_attempt_at": stale, "kind": "my_open_experiments",
        "object_id": "exp-resolved",
    }
    initial = len(dead_events)
    assert initial >= 200
    state_file.write_text(
        json.dumps({"schema_version": 1, "personas": {"host": {"events": dead_events}}}),
        encoding="utf-8",
    )
    worker = RuntimeWaker(
        client=FakeMapClient(persona="host", todos={}),
        config=RuntimeWakerConfig(
            persona="host", state_file=state_file, project_root=Path.cwd(),
            cooldown_seconds=60, woken_cooldown_seconds=60,
        ),
        backend=FakeWakeBackend(),
    )

    stats1 = worker.run_once()
    events1 = json.loads(state_file.read_text(encoding="utf-8"))["personas"]["host"]["events"]
    assert stats1.events_pruned == initial
    assert len(events1) == 0  # all orphans gone; nothing live to retain

    # Cycles 2 and 3: stable — no new dead entries, the count never grows back.
    stats2 = worker.run_once()
    events2 = json.loads(state_file.read_text(encoding="utf-8"))["personas"]["host"]["events"]
    stats3 = worker.run_once()
    events3 = json.loads(state_file.read_text(encoding="utf-8"))["personas"]["host"]["events"]
    assert (stats2.events_pruned, stats3.events_pruned) == (0, 0)
    assert len(events2) == len(events3) == len(events1) == 0


def test_prune_keeps_entries_within_ttl(tmp_path):
    """A2: an entry whose last_attempt_at is inside the TTL window must survive."""
    state_file = tmp_path / "state.json"
    fresh = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()  # half of the 60s window
    state_file.write_text(
        json.dumps({"schema_version": 1, "personas": {"host": {"events": {
            "host:pending_topic_replies:comment-1": {"status": "woken", "last_attempt_at": fresh},
        }}}}),
        encoding="utf-8",
    )
    worker = RuntimeWaker(
        client=FakeMapClient(persona="host", todos={}),
        config=RuntimeWakerConfig(
            persona="host", state_file=state_file, project_root=Path.cwd(),
            cooldown_seconds=60, woken_cooldown_seconds=60,
        ),
        backend=FakeWakeBackend(),
    )

    stats = worker.run_once()
    events = json.loads(state_file.read_text(encoding="utf-8"))["personas"]["host"]["events"]
    assert "host:pending_topic_replies:comment-1" in events
    assert stats.events_pruned == 0


def test_prune_dry_run_does_not_write_state(tmp_path):
    """A3: --dry-run reports the would-prune count but leaves the state file untouched."""
    state_file = tmp_path / "state.json"
    stale = (datetime.now(UTC) - timedelta(seconds=3600)).isoformat()
    payload = {"schema_version": 1, "personas": {"host": {"events": {
        f"host:pending_topic_reply:topic-1:comment-{i}": {"status": "woken", "last_attempt_at": stale}
        for i in range(5)
    }}}}
    state_file.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    before = state_file.read_bytes()

    worker = RuntimeWaker(
        client=FakeMapClient(persona="host", todos={}),
        config=RuntimeWakerConfig(
            persona="host", state_file=state_file, project_root=Path.cwd(),
            cooldown_seconds=60, woken_cooldown_seconds=60, dry_run=True,
        ),
        backend=FakeWakeBackend(),
    )

    stats = worker.run_once()
    after = state_file.read_bytes()

    assert stats.events_pruned == 5  # surfaced via the cycle summary stats
    assert after == before  # dry-run contract: never write
    assert len(json.loads(after)["personas"]["host"]["events"]) == 5


def test_cycle_summary_reports_events_pruned_sync(tmp_path, monkeypatch):
    """A4 (sync loop): events_pruned is in the cycle-summary fields and populated."""
    captured: dict[str, Any] = {}

    def fake_log(name, stats, *, fields):
        captured["fields"] = list(fields)
        captured["events_pruned"] = stats.events_pruned

    monkeypatch.setattr(runtime_waker, "log_cycle_summary", fake_log)

    state_file = tmp_path / "state.json"
    stale = (datetime.now(UTC) - timedelta(seconds=3600)).isoformat()
    state_file.write_text(
        json.dumps({"schema_version": 1, "personas": {"host": {"events": {
            f"host:pending_topic_reply:topic-1:comment-{i}": {"status": "woken", "last_attempt_at": stale}
            for i in range(3)
        }}}}),
        encoding="utf-8",
    )
    worker = RuntimeWaker(
        client=FakeMapClient(persona="host", todos={}),
        config=RuntimeWakerConfig(
            persona="host", once=True, state_file=state_file, project_root=Path.cwd(),
            cooldown_seconds=60, woken_cooldown_seconds=60,
        ),
        backend=FakeWakeBackend(),
    )

    worker.run_forever()

    assert "events_pruned" in captured["fields"]
    assert captured["events_pruned"] == 3


def test_waker_passes_wake_event_metadata_to_session_log(tmp_path, monkeypatch):
    """D5: the waker's WakeEvent.event_id / fingerprint reach the sessions jsonl.

    Without this, A3 (notification.id ↔ inbound_event.event_id ↔ sessions
    jsonl.event_id) cannot be assembled. We assert the waker-derived event_id
    equals the UUID5 of (agent_id, fingerprint) — same value used by D6 to
    record the inbound_event row, so the join keys align.
    """
    import asyncio
    import uuid

    captured_wake_kwargs: dict[str, Any] = {}

    class CapturingAgentClient:
        def __init__(self, state: dict[str, Any]) -> None:
            self.state = state

        async def connect(self) -> None:
            return None

        async def disconnect(self) -> None:
            return None

        async def wake_up(self, prompt: str, **_kwargs: Any) -> str:
            captured_wake_kwargs.update(_kwargs)
            self.state["claude_session_id"] = "sid-from-wake"
            return "ok"

    state_file = tmp_path / "state.json"
    backend = runtime_waker.PersonaAgentWakeBackend(
        project_root=Path.cwd(),
        persona="host",
        get_agent_state=lambda: {},
        save_state_fn=lambda: None,
    )
    backend._agent_client = CapturingAgentClient({"claude_session_id": None})  # noqa: SLF001

    client = FakeMapClient(
        persona="host",
        todos=_host_topic_todos(),
    )
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="host",
            once=True,
            state_file=state_file,
            project_root=Path.cwd(),
            cooldown_seconds=60,
        ),
        backend=backend,
    )

    asyncio.run(worker._run_once_async())  # noqa: SLF001

    # D6 used the UUID5 of (agent_id, fingerprint) — the same value must show
    # up as event_id in the wake_up kwargs so the sessions jsonl row joins on
    # the inbound_event row recorded by D6.
    expected_event_id = str(
        uuid.uuid5(
            runtime_waker._EVENT_UUID_NAMESPACE,
            f"{client.persona}-agent:host:pending_topic_replies:comment-1",
        )
    )
    assert captured_wake_kwargs["event_id"] == expected_event_id
    assert captured_wake_kwargs["event_source"] == "polling"
    assert captured_wake_kwargs["fingerprint"] == "host:pending_topic_replies:comment-1"


def test_cycle_summary_reports_events_pruned_claude(tmp_path, monkeypatch):
    """A4 (claude loop): events_pruned is in the claude cycle-summary fields too."""
    import asyncio

    captured: dict[str, Any] = {}

    def fake_log(name, stats, *, fields):
        captured["fields"] = list(fields)
        captured["events_pruned"] = stats.events_pruned

    monkeypatch.setattr(runtime_waker, "log_cycle_summary", fake_log)

    class FakeAgentClient:
        async def connect(self) -> None:
            return None

        async def disconnect(self) -> None:
            return None

        async def wake_up(self, prompt: str, **_kwargs: Any) -> str:
            return "ok"

    state_file = tmp_path / "state.json"
    stale = (datetime.now(UTC) - timedelta(seconds=3600)).isoformat()
    state_file.write_text(
        json.dumps({"schema_version": 1, "personas": {"host": {"events": {
            f"host:pending_topic_reply:topic-1:comment-{i}": {"status": "woken", "last_attempt_at": stale}
            for i in range(2)
        }}}}),
        encoding="utf-8",
    )
    backend = runtime_waker.PersonaAgentWakeBackend(
        project_root=Path.cwd(),
        persona="host",
        get_agent_state=lambda: {},
        save_state_fn=lambda: None,
    )
    backend._agent_client = FakeAgentClient()  # noqa: SLF001
    worker = RuntimeWaker(
        client=FakeMapClient(persona="host", todos={}),
        config=RuntimeWakerConfig(
            persona="host", once=True, state_file=state_file, project_root=Path.cwd(),
            cooldown_seconds=60, woken_cooldown_seconds=60,
        ),
        backend=backend,
    )

    asyncio.run(worker._run_forever_claude())  # noqa: SLF001

    assert "events_pruned" in captured["fields"]
    assert captured["events_pruned"] == 2


def test_no_prune_events_escape_hatch(tmp_path):
    """A5: --no-prune-events disables the sweep — nothing pruned, state untouched."""
    state_file = tmp_path / "state.json"
    stale = (datetime.now(UTC) - timedelta(seconds=3600)).isoformat()
    payload = {"schema_version": 1, "personas": {"host": {"events": {
        f"host:pending_topic_reply:topic-1:comment-{i}": {"status": "woken", "last_attempt_at": stale}
        for i in range(4)
    }}}}
    state_file.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    before = state_file.read_bytes()

    worker = RuntimeWaker(
        client=FakeMapClient(persona="host", todos={}),
        config=RuntimeWakerConfig(
            persona="host", state_file=state_file, project_root=Path.cwd(),
            cooldown_seconds=60, woken_cooldown_seconds=60, prune_events=False,
        ),
        backend=FakeWakeBackend(),
    )

    stats = worker.run_once()
    after = state_file.read_bytes()

    assert stats.events_pruned == 0
    assert after == before  # escape hatch: sweep never mutates state


# --- D6 / D3 / D2 regression tests for runtime-waker inbound-event integration ---


class _OrderCapturingBackend:
    """Backend that records the on-disk state file at the moment wake() is called.

    The D3 contract is: ``_mark_event(status="woken")`` + ``_save_state_if_needed``
    must run **before** ``backend.wake()`` so a crash mid-resume does not let the
    next start re-wake the same fingerprint. The state path is set by the test
    harness via :py:meth:`set_state_file` so we can read it at wake() entry.
    """

    _state_file: Path | None = None

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.state_at_call: list[dict[str, Any]] = []

    @classmethod
    def set_state_file(cls, path: Path) -> None:
        cls._state_file = path

    def wake(self, *, persona: str, prompt: str, session_id: str | None) -> WakeResult:
        self.calls.append({"persona": persona, "prompt": prompt, "session_id": session_id})
        path = type(self)._state_file
        if path is not None and path.exists():
            self.state_at_call.append(json.loads(path.read_text(encoding="utf-8")))
        else:
            self.state_at_call.append({})
        return WakeResult(session_id=f"session-{persona}", response_text="done")


def test_waker_persists_woken_state_before_resume(tmp_path):
    """D3 regression: dedup state is on disk before the backend resumes."""
    state_file = tmp_path / "runtime-waker-state.json"
    _OrderCapturingBackend.set_state_file(state_file)
    backend = _OrderCapturingBackend()
    client = FakeMapClient(
        persona="host",
        todos=_host_topic_todos(),
    )
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="host", once=True, state_file=state_file, project_root=Path.cwd(),
            cooldown_seconds=60,
        ),
        backend=backend,
    )

    worker.run_once()

    assert len(backend.calls) == 1, "resume must happen exactly once"
    assert len(backend.state_at_call) == 1
    # Read the snapshot the backend captured at wake() entry. The state file is
    # also re-read end-of-cycle, but the snapshot proves the order: status=woken
    # was persisted BEFORE wake().
    state = backend.state_at_call[0]
    record = state["personas"]["host"]["events"]["host:pending_topic_replies:comment-1"]
    assert record["status"] == "woken"
    assert "woken_at" in record

    # The on-disk state file must also reflect the woken stamp by the time
    # backend.wake() returns.
    on_disk = json.loads(state_file.read_text(encoding="utf-8"))
    assert (
        on_disk["personas"]["host"]["events"][
            "host:pending_topic_replies:comment-1"
        ]["status"]
        == "woken"
    )


def test_waker_records_inbound_event_before_resume(tmp_path):
    """D6 + D3 combined: server record call precedes backend.wake()."""
    state_file = tmp_path / "runtime-waker-state.json"
    _OrderCapturingBackend.set_state_file(state_file)
    backend = _OrderCapturingBackend()
    client = FakeMapClient(
        persona="host",
        todos=_host_topic_todos(),
    )
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="host", once=True, state_file=state_file, project_root=Path.cwd(),
            cooldown_seconds=60,
        ),
        backend=backend,
    )

    worker.run_once()

    assert len(client.record_calls) == 1
    record = client.record_calls[0]
    assert record["fingerprint"] == "host:pending_topic_replies:comment-1"
    assert record["event_type"] == "pending_topic_replies"
    assert record["source"] == "polling"
    # event_id is a UUID5 derived from (agent_id, fingerprint).
    import uuid as _uuid

    parsed = _uuid.UUID(record["event_id"])
    # Calling uuid5 with the same namespace+name must yield the same UUID.
    assert parsed == _uuid.uuid5(
        _uuid.UUID("00000000-0000-0000-0000-000000000001"),
        "host-agent:host:pending_topic_replies:comment-1",
    )


def test_waker_skips_resume_when_server_says_duplicate(tmp_path):
    """D6: server gate 409 → backend never resumes, event marked server_skip."""
    state_file = tmp_path / "runtime-waker-state.json"
    _OrderCapturingBackend.set_state_file(state_file)
    backend = _OrderCapturingBackend()
    client = FakeMapClient(
        persona="host",
        todos=_host_topic_todos(),
        duplicate_fingerprints={"host:pending_topic_replies:comment-1"},
    )
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="host", once=True, state_file=state_file, project_root=Path.cwd(),
            cooldown_seconds=60,
        ),
        backend=backend,
    )

    worker.run_once()

    assert backend.calls == [], "resume must be skipped when server returns duplicate"
    assert len(client.record_calls) == 1
    state = json.loads(state_file.read_text(encoding="utf-8"))
    record = state["personas"]["host"]["events"]["host:pending_topic_replies:comment-1"]
    assert record["status"] == "server_skip"
    # No woken stamp — this is a duplicate claim, not our own success.
    assert "woken_at" not in record


def test_server_skip_uses_self_heal_ttl(tmp_path):
    """server_skip must use the long self-heal TTL (woken_cooldown_seconds), not the short one."""
    recent = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    state_file = tmp_path / "runtime-waker-state.json"
    state_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "personas": {
                    "host": {
                        "events": {
                            "host:pending_topic_replies:comment-1": {
                                "status": "server_skip",
                                "last_attempt_at": recent,
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    backend = FakeWakeBackend()
    client = FakeMapClient(
        persona="host",
        todos=_host_topic_todos(),
    )
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="host", once=True, state_file=state_file, project_root=Path.cwd(),
            cooldown_seconds=300, woken_cooldown_seconds=1800,
        ),
        backend=backend,
    )

    stats = worker.run_once()

    # Within the 1800s TTL, the event is skipped even though it would have
    # passed the 300s cooldown if status were treated as a normal skip.
    assert stats.wake_skips == 1
    assert backend.calls == []


def test_server_skip_heartbeat_resumes_after_ttl(tmp_path):
    """After heartbeat TTL, server 409 must not block resume (unfinished todos)."""
    stale = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
    state_file = tmp_path / "runtime-waker-state.json"
    state_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "personas": {
                    "host": {
                        "events": {
                            "host:my_open_experiments:exp-1": {
                                "status": "server_skip",
                                "last_attempt_at": stale,
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    backend = FakeWakeBackend()
    client = FakeMapClient(
        persona="host",
        todos={
            "my_open_experiments": [
                {
                    "id": "exp-1",
                    "phase": "running",
                    "current_plan_version": 1,
                    "open_unreasonable_count": 0,
                }
            ]
        },
        duplicate_fingerprints={"host:my_open_experiments:exp-1"},
    )
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="host",
            once=True,
            state_file=state_file,
            project_root=Path.cwd(),
            heartbeat_seconds=30,
        ),
        backend=backend,
    )

    stats = worker.run_once()

    assert stats.wakes_sent == 1
    assert len(backend.calls) == 1
    state = json.loads(state_file.read_text(encoding="utf-8"))
    record = state["personas"]["host"]["events"]["host:my_open_experiments:exp-1"]
    assert record["status"] == "woken"


def test_persona_inflight_skips_when_seeded_recent(tmp_path):
    # A persona-level wake during THIS process (after startup) suppresses the
    # persona's other events within the inflight window.
    state_file = tmp_path / "runtime-waker-state.json"
    state_file.write_text(
        json.dumps({"schema_version": 1, "personas": {"host": {}}}),
        encoding="utf-8",
    )
    backend = FakeWakeBackend()
    client = FakeMapClient(persona="host", todos=_host_topic_todos())
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="host",
            state_file=state_file,
            project_root=Path.cwd(),
            persona_inflight_seconds=1800,
        ),
        backend=backend,
    )
    # Simulate a wake that happened during this process (>= _started_at).
    # Writing last_woken_at into the state file BEFORE construction would be
    # treated as a previous-process wake and ignored — see test below.
    worker.state["personas"]["host"]["last_woken_at"] = datetime.now(UTC).isoformat()

    stats = worker.run_once()

    assert stats.wake_skips == 1
    assert stats.wakes_sent == 0
    assert backend.calls == []


def test_persona_inflight_ignores_wake_from_previous_process(tmp_path):
    # last_woken_at persisted by a PREVIOUS waker process must not suppress a
    # freshly started process — inflight only counts wakes from this process,
    # so a restart no longer idles the persona for the whole window.
    stale = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    state_file = tmp_path / "runtime-waker-state.json"
    state_file.write_text(
        json.dumps({"schema_version": 1, "personas": {"host": {"last_woken_at": stale}}}),
        encoding="utf-8",
    )
    backend = FakeWakeBackend()
    client = FakeMapClient(persona="host", todos=_host_topic_todos())
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="host",
            state_file=state_file,
            project_root=Path.cwd(),
            persona_inflight_seconds=1800,
        ),
        backend=backend,
    )

    stats = worker.run_once()

    # Stale wake predates this process -> not suppressed.
    assert stats.wakes_sent == 1
    assert len(backend.calls) == 1


def test_persona_inflight_expires(tmp_path):
    # Once last_woken_at is older than persona_inflight_seconds the gate opens
    # again; an event with no per-event dedup record is woken.
    stale = (datetime.now(UTC) - timedelta(seconds=1900)).isoformat()
    state_file = tmp_path / "runtime-waker-state.json"
    state_file.write_text(
        json.dumps({"schema_version": 1, "personas": {"host": {"last_woken_at": stale}}}),
        encoding="utf-8",
    )
    backend = FakeWakeBackend()
    client = FakeMapClient(persona="host", todos=_host_topic_todos())
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="host",
            state_file=state_file,
            project_root=Path.cwd(),
            persona_inflight_seconds=1800,
        ),
        backend=backend,
    )

    stats = worker.run_once()

    assert stats.wakes_sent == 1
    assert len(backend.calls) == 1


def test_persona_inflight_zero_disables(tmp_path):
    # persona_inflight_seconds=0 falls back to pure per-event dedup: a recent
    # last_woken_at does not block an event with no prior record.
    recent = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    state_file = tmp_path / "runtime-waker-state.json"
    state_file.write_text(
        json.dumps({"schema_version": 1, "personas": {"host": {"last_woken_at": recent}}}),
        encoding="utf-8",
    )
    backend = FakeWakeBackend()
    client = FakeMapClient(persona="host", todos=_host_topic_todos())
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="host",
            state_file=state_file,
            project_root=Path.cwd(),
            persona_inflight_seconds=0,
        ),
        backend=backend,
    )

    stats = worker.run_once()

    assert stats.wakes_sent == 1
    assert len(backend.calls) == 1


def test_persona_inflight_bypassed_by_force(tmp_path):
    # force bypasses _should_skip_event at the outer _run_once_async gate, so a
    # recent inflight stamp does not block the wake.
    recent = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    state_file = tmp_path / "runtime-waker-state.json"
    state_file.write_text(
        json.dumps({"schema_version": 1, "personas": {"host": {"last_woken_at": recent}}}),
        encoding="utf-8",
    )
    backend = FakeWakeBackend()
    client = FakeMapClient(persona="host", todos=_host_topic_todos())
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="host",
            state_file=state_file,
            project_root=Path.cwd(),
            persona_inflight_seconds=1800,
            force=True,
        ),
        backend=backend,
    )

    stats = worker.run_once()

    assert stats.wakes_sent == 1
    assert len(backend.calls) == 1


def test_persona_inflight_isolated_per_persona(tmp_path):
    # A host-side last_woken_at must not suppress a participant event: the gate
    # keys on the event's own persona state.
    recent = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    state_file = tmp_path / "runtime-waker-state.json"
    state_file.write_text(
        json.dumps({"schema_version": 1, "personas": {"host": {"last_woken_at": recent}}}),
        encoding="utf-8",
    )
    backend = FakeWakeBackend()
    client = FakeMapClient(persona="participant", todos=_host_topic_todos())
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="participant",
            state_file=state_file,
            project_root=Path.cwd(),
            persona_inflight_seconds=1800,
        ),
        backend=backend,
    )

    stats = worker.run_once()

    assert stats.wakes_sent == 1
    assert len(backend.calls) == 1


def test_persona_inflight_skips_other_event_after_successful_wake(tmp_path):
    # End-to-end: waking event A stamps personas.host.last_woken_at; on the next
    # cycle a sibling event B is skipped by single-flight, proving a running
    # session is not preempted by a different-context wake.
    state_file = tmp_path / "runtime-waker-state.json"
    two_topic_todos = {
        "pending_topic_replies": [
            {
                "topic_id": "topic-1",
                "comment_id": "comment-1",
                "topic_title": "T1",
                "author_agent_id": "participant-1",
                "author_name": "p",
                "excerpt": "a",
                "created_at": "2026-07-02T10:00:00+00:00",
            },
            {
                "topic_id": "topic-2",
                "comment_id": "comment-2",
                "topic_title": "T2",
                "author_agent_id": "participant-1",
                "author_name": "p",
                "excerpt": "b",
                "created_at": "2026-07-02T10:00:00+00:00",
            },
        ]
    }
    backend = FakeWakeBackend()
    client = FakeMapClient(persona="host", todos=two_topic_todos)
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="host",
            state_file=state_file,
            project_root=Path.cwd(),
            persona_inflight_seconds=1800,
            max_wakes_per_cycle=1,
        ),
        backend=backend,
    )

    first = worker.run_once()
    assert first.wakes_sent == 1
    # The successful wake must persist the persona-level inflight stamp.
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert "last_woken_at" in state["personas"]["host"]

    # Second cycle: both A (woken) and B (no prior record) are skipped by the
    # persona single-flight gate; neither is woken again.
    second = worker.run_once()
    assert second.wakes_sent == 0
    assert second.wake_skips == 2
    assert len(backend.calls) == 1  # only A was ever woken; B was not



