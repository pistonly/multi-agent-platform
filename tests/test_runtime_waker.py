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


class FakeMapClient(MapCommandClient):
    def __init__(
        self,
        *,
        persona: str,
        todos: dict[str, Any],
        open_topics: list[dict[str, Any]] | None = None,
    ) -> None:
        self.persona = persona
        self._todos = todos
        self._open_topics = open_topics or []

    def whoami(self) -> dict[str, Any]:
        return {"id": f"{self.persona}-agent", "name": self.persona}

    def todos(self) -> dict[str, Any]:
        return self._todos

    def topic_list_open(self) -> list[dict[str, Any]]:
        return self._open_topics


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
                {"topic_id": "topic-1", "comment_id": "comment-1", "topic_title": "T"}
            ],
            "my_open_topics": [{"id": "topic-2", "title": "Open", "discussion_round": "round1"}],
            "my_open_experiments": [
                {"id": "exp-1", "title": "Exp", "phase": "review", "current_plan_version": 2}
            ],
        },
    )

    assert [event.kind for event in events] == [
        "pending_topic_reply",
        "topic_lifecycle",
        "experiment_lifecycle",
    ]
    assert events[0].fingerprint == "host:pending_topic_reply:topic-1:comment-1"
    assert events[2].fingerprint == "host:experiment_lifecycle:exp-1:review:v2:u"


def test_experiment_lifecycle_fingerprint_includes_open_unreasonable_count():
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
    assert events[0].fingerprint == "host:experiment_lifecycle:exp-1:review:v1:u9"


def test_experiment_lifecycle_fingerprint_zero_open_count_is_explicit():
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
    assert events[0].fingerprint == "host:experiment_lifecycle:exp-1:review:v1:u0"


def test_discover_reviewer_and_participant_events():
    reviewer_events = discover_wake_events(
        "reviewer",
        {
            "pending_reviews": [{"id": "exp-1", "current_plan_version": 3}],
            "pending_result_reviews": [{"id": "exp-2", "phase": "result_review", "updated_at": "t1"}],
            "pending_replies": [{"item_id": "item-1", "status": "addressed"}],
            "mentions": [{"topic_id": "topic-2", "source_id": "comment-9"}],
        },
    )
    participant_events = discover_wake_events(
        "participant",
        {"mentions": [{"topic_id": "topic-1", "source_id": "comment-1"}]},
    )

    assert [event.kind for event in reviewer_events] == [
        "mention",
        "pending_review",
        "pending_result_review",
        "addressed_review_item",
    ]
    assert reviewer_events[0].fingerprint == "reviewer:mention:topic-2:comment-9"
    assert reviewer_events[1].fingerprint == "reviewer:pending_review:exp-1:v3"
    assert reviewer_events[2].fingerprint == "reviewer:pending_result_review:exp-2:t1"
    assert participant_events[0].fingerprint == "participant:mention:topic-1:comment-1"


def test_discover_round_ack_pending_events():
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

    assert [event.kind for event in participant_events] == ["round_ack_pending"]
    assert participant_events[0].fingerprint.startswith("participant:round_ack_pending:topic-ack:")
    assert reviewer_events[0].persona == "reviewer"
    prompt = build_wake_prompt(participant_events[0], project_root=Path.cwd())
    assert "topic advance-round" in prompt
    assert "--ack accept" in prompt


def test_runtime_waker_wakes_once_and_persists_session(tmp_path):
    state_file = tmp_path / "runtime-waker-state.json"
    backend = FakeWakeBackend()
    client = FakeMapClient(
        persona="host",
        todos={"pending_topic_replies": [{"topic_id": "topic-1", "comment_id": "comment-1"}]},
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
    assert state["personas"]["host"]["events"]["host:pending_topic_reply:topic-1:comment-1"]["status"] == "woken"


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
                            "host:pending_topic_reply:topic-1:comment-1": {
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
        todos={"pending_topic_replies": [{"topic_id": "topic-1", "comment_id": "comment-1"}]},
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
                            "host:pending_topic_reply:topic-1:comment-1": {
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
        todos={"pending_topic_replies": [{"topic_id": "topic-1", "comment_id": "comment-1"}]},
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
    record = state["personas"]["host"]["events"]["host:pending_topic_reply:topic-1:comment-1"]
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
                            "host:pending_topic_reply:topic-1:comment-1": {
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
        todos={"pending_topic_replies": [{"topic_id": "topic-1", "comment_id": "comment-1"}]},
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
        todos={"pending_topic_replies": [{"topic_id": "topic-1", "comment_id": "comment-1"}]},
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
        {"pending_topic_replies": [{"topic_id": "topic-a", "comment_id": "comment-1"}]},
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
        todos={"pending_topic_replies": [{"topic_id": "topic-b", "comment_id": "comment-1"}]},
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
        todos={"pending_topic_replies": [{"topic_id": "topic-a", "comment_id": "comment-2"}]},
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

        async def wake_up(self, prompt: str) -> str:
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
            todos={"pending_topic_replies": [{"topic_id": "topic-b", "comment_id": "comment-1"}]},
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
        todos={},
        open_topics=[
            {
                "id": "topic-open",
                "title": "Open",
                "updated_at": "2026-06-30T00:00:00Z",
                "comment_count": 0,
            }
        ],
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
    assert "MAP wake · open_topic_opportunity · topic-open" in backend.calls[0]["prompt"]


def test_participant_skips_open_topic_when_latest_comment_is_self():
    participant_id = "participant-agent"
    events = discover_wake_events(
        "participant",
        {
            "open_topics": [
                {
                    "id": "topic-1",
                    "title": "T",
                    "comment_count": 2,
                    "last_comment_id": "comment-2",
                    "last_comment_author_agent_id": participant_id,
                }
            ]
        },
        participant_agent_id=participant_id,
    )
    assert events == []


def test_participant_skips_open_topic_without_last_comment_id():
    events = discover_wake_events(
        "participant",
        {
            "open_topics": [
                {
                    "id": "topic-1",
                    "title": "T",
                    "comment_count": 2,
                    "last_comment_author_agent_id": "host-agent",
                }
            ]
        },
        participant_agent_id="participant-agent",
    )
    assert events == []


def test_participant_skips_round1_when_already_spoke_twice_without_summary():
    participant_id = "participant-agent"
    events = discover_wake_events(
        "participant",
        {
            "open_topics": [
                {
                    "id": "topic-1",
                    "title": "T",
                    "discussion_round": "round1",
                    "round_summary_count": 0,
                    "my_comment_count": 2,
                    "comment_count": 4,
                    "last_comment_id": "comment-4",
                    "last_comment_author_agent_id": "host-agent",
                }
            ]
        },
        participant_agent_id=participant_id,
    )
    assert events == []


def test_participant_wakes_for_open_topic_when_latest_comment_is_other():
    participant_id = "participant-agent"
    events = discover_wake_events(
        "participant",
        {
            "open_topics": [
                {
                    "id": "topic-1",
                    "title": "T",
                    "comment_count": 1,
                    "last_comment_id": "comment-1",
                    "last_comment_author_agent_id": "host-agent",
                    "last_comment_author_name": "multi-agents-platform-host",
                    "last_comment_excerpt": "host 开场",
                }
            ]
        },
        participant_agent_id=participant_id,
    )
    assert len(events) == 1
    assert events[0].kind == "open_topic_opportunity"
    assert events[0].fingerprint == "participant:open_topic:topic-1:comment-1"


def test_participant_waker_can_scan_open_topics_when_enabled(tmp_path):
    backend = FakeWakeBackend()
    client = FakeMapClient(
        persona="participant",
        todos={},
        open_topics=[{"id": "topic-open", "title": "Open", "updated_at": "2026-06-30T00:00:00Z"}],
    )
    worker = RuntimeWaker(
        client=client,
        config=RuntimeWakerConfig(
            persona="participant",
            state_file=tmp_path / "state.json",
            project_root=Path.cwd(),
            include_participant_open_topics=True,
        ),
        backend=backend,
    )

    stats = worker.run_once()

    assert stats.events_seen == 1
    assert stats.wakes_sent == 1
    assert "MAP wake · open_topic_opportunity · topic-open" in backend.calls[0]["prompt"]


def test_build_wake_prompt_is_short_and_cli_oriented():
    event = discover_wake_events(
        "host",
        {
            "pending_topic_replies": [
                {
                    "topic_id": "topic-1",
                    "comment_id": "comment-1",
                    "topic_title": "T",
                    "author_name": "multi-agents-platform-participant",
                    "excerpt": "需要 host 回复",
                }
            ]
        },
    )[0]

    prompt = build_wake_prompt(event, project_root=Path("/tmp/multi_agents_platform"))

    assert prompt.startswith("MAP wake · pending_topic_reply · topic-1")
    assert "latest_by=multi-agents-platform-participant" in prompt
    assert "excerpt=需要 host 回复" in prompt
    assert "reply_to=comment-1" in prompt
    assert "map --persona host topic show --id topic-1" in prompt
    assert "不要使用 MCP" not in prompt
    assert "```json" not in prompt
    assert len(prompt) < 400


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
    """With 3 topic_lifecycle + 2 experiment_lifecycle at limit=3, round-robin
    must give experiment_lifecycle a slot instead of letting topics starve it."""
    topics = [
        runtime_waker.WakeEvent(
            persona="host", kind="topic_lifecycle", object_id=f"t{i}", fingerprint=f"t{i}"
        )
        for i in range(3)
    ]
    experiments = [
        runtime_waker.WakeEvent(
            persona="host", kind="experiment_lifecycle", object_id=f"e{i}", fingerprint=f"e{i}"
        )
        for i in range(2)
    ]
    selected = runtime_waker._round_robin_by_kind(topics + experiments, limit=3)
    assert len(selected) == 3
    kinds = [e.kind for e in selected]
    # first pass takes one of each kind, then a second topic
    assert kinds == ["topic_lifecycle", "experiment_lifecycle", "topic_lifecycle"]


def test_round_robin_by_kind_limit_zero():
    assert runtime_waker._round_robin_by_kind(
        [
            runtime_waker.WakeEvent(
                persona="host", kind="topic_lifecycle", object_id="t", fingerprint="t"
            )
        ],
        limit=0,
    ) == []


def test_host_cycle_does_not_starve_experiments(tmp_path):
    """3 open topics must not monopolize a 3-wake cycle and starve 2 experiments.

    Regression for the deadlock where host kept waking only topic_lifecycle events
    and never reached experiment_lifecycle, so experiments stuck in review never
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
    assert any("experiment_lifecycle" in p for p in woken_prompts), (
        "experiment_lifecycle starved by topic_lifecycle"
    )
    assert any("topic_lifecycle" in p for p in woken_prompts)
