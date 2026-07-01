"""Tests for cli.agent_client — persistent in-process Claude SDK client."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from cli.agent_client import PersonaAgentClient, make_wakeup_prompt


# --- Fakes -------------------------------------------------------------------


class FakeResultMessage(ResultMessage):
    """Minimal ResultMessage for tests — fills defaults for unused fields."""

    def __init__(self, *, session_id: str, is_error: bool = False) -> None:
        super().__init__(
            subtype="",
            duration_ms=0,
            duration_api_ms=0,
            is_error=is_error,
            num_turns=1,
            session_id=session_id,
        )


class FakeTextBlock(TextBlock):
    """TextBlock subclass with required text field."""

    def __init__(self, text: str) -> None:
        super().__init__(text=text)


class FakeAssistantMessage(AssistantMessage):
    def __init__(self, *blocks: Any) -> None:
        super().__init__(content=list(blocks), model="claude-test-model")


class FakeReceive:
    """Async iterator of canned messages passed to receive_response()."""

    def __init__(self, messages: list[Any]) -> None:
        self._messages = list(messages)

    def __aiter__(self) -> "FakeReceive":
        self._iter = iter(self._messages)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._iter)
        except StopIteration as exc:  # noqa: F841
            raise StopAsyncIteration


class FakeClaudeClient:
    """Records calls; configurable canned response."""

    def __init__(self, *, messages: list[Any] | None = None, raise_on_connect: Exception | None = None) -> None:
        self.messages = messages or []
        self.raise_on_connect = raise_on_connect
        self.connected_with: Any = None
        self.disconnect_calls = 0
        self.query_calls: list[str] = []
        self.options: Any = None
        self.kwargs: Any = None

    async def connect(self, prompt: str | None = None) -> None:
        if self.raise_on_connect is not None:
            raise self.raise_on_connect
        self.connected_with = prompt
        self.options = getattr(self, "_captured_options", None)

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def query(self, prompt: str) -> None:
        self.query_calls.append(prompt)

    def receive_response(self) -> Any:
        return FakeReceive(self.messages)


def _make_client(
    *,
    state: dict[str, Any],
    project_root: Path,
    messages: list[Any] | None = None,
) -> tuple[PersonaAgentClient, FakeClaudeClient]:
    fake = FakeClaudeClient(messages=messages)
    save_calls: list[int] = []

    def save() -> None:
        save_calls.append(1)

    agent = PersonaAgentClient(
        persona="host",
        state=state,
        save_state_fn=save,
        project_root=project_root,
        extra_env={},
        model="claude-test-model",
        _client_factory=lambda options: _capture_then_return(options, fake),
    )
    return agent, fake


def _capture_then_return(options: Any, fake: FakeClaudeClient) -> FakeClaudeClient:
    fake._captured_options = options  # type: ignore[attr-defined]
    return fake


# --- resume vs first-start options ------------------------------------------


def test_connect_uses_resume_when_state_has_session_id(tmp_path: Path) -> None:
    state = {"claude_session_id": "abc-123", "topics": {}, "experiments": {}}

    agent, fake = _make_client(state=state, project_root=tmp_path)

    import asyncio

    asyncio.run(agent.connect())

    assert fake.options is not None
    assert fake.options.resume == "abc-123"


def test_connect_skips_resume_when_state_has_no_session(tmp_path: Path) -> None:
    state = {"topics": {}, "experiments": {}}

    agent, fake = _make_client(state=state, project_root=tmp_path)

    import asyncio

    asyncio.run(agent.connect())

    assert fake.options is not None
    resume = getattr(fake.options, "resume", None)
    assert resume in (None, "")


def test_options_includes_required_fields(tmp_path: Path) -> None:
    state = {"claude_session_id": "s1", "topics": {}, "experiments": {}}

    agent, fake = _make_client(state=state, project_root=tmp_path)

    import asyncio

    asyncio.run(agent.connect())

    opts = fake.options
    assert opts.cwd == str(tmp_path)
    assert opts.setting_sources == ["project"]
    assert "Skill" in opts.allowed_tools
    assert "Bash" in opts.allowed_tools
    assert opts.permission_mode == "acceptEdits"
    assert opts.model == "claude-test-model"


def test_system_prompt_appends_persona_directive(tmp_path: Path) -> None:
    state = {"topics": {}, "experiments": {}}

    agent, fake = _make_client(state=state, project_root=tmp_path)

    import asyncio

    asyncio.run(agent.connect())

    sp = fake.options.system_prompt
    # ClaudeAgentOptions accepts dict {type, preset, append}
    assert isinstance(sp, dict)
    assert sp.get("type") == "preset"
    assert sp.get("preset") == "claude_code"
    append = sp.get("append") or ""
    assert "host" in append
    assert "map --persona host todos" in append


# --- wake_up behavior --------------------------------------------------------


def test_wake_up_persists_session_id_when_changed(tmp_path: Path) -> None:
    state = {"topics": {}, "experiments": {}, "claude_session_id": "old"}
    messages = [
        FakeAssistantMessage(FakeTextBlock("hello")),
        FakeResultMessage(session_id="new-session-id", is_error=False),
    ]

    agent, fake = _make_client(state=state, project_root=tmp_path, messages=messages)

    import asyncio

    status = asyncio.run(agent.wake_up("check todos"))

    assert status == "ok"
    assert state["claude_session_id"] == "new-session-id"
    assert state["last_wakeup_status"] == "ok"
    assert "last_wakeup_at" in state
    assert fake.query_calls == ["check todos"]


def test_wake_up_keeps_existing_session_id_when_unchanged(tmp_path: Path) -> None:
    state = {"topics": {}, "experiments": {}, "claude_session_id": "stable"}
    messages = [FakeResultMessage(session_id="stable", is_error=False)]

    agent, _ = _make_client(state=state, project_root=tmp_path, messages=messages)

    import asyncio

    status = asyncio.run(agent.wake_up("again"))

    assert status == "ok"
    assert state["claude_session_id"] == "stable"


def test_wake_up_returns_error_when_result_is_error(tmp_path: Path) -> None:
    state = {"topics": {}, "experiments": {}}
    messages = [FakeResultMessage(session_id="sid", is_error=True)]

    agent, _ = _make_client(state=state, project_root=tmp_path, messages=messages)

    import asyncio

    status = asyncio.run(agent.wake_up("oops"))

    assert status == "error"
    assert state["last_wakeup_status"] == "error"


def test_wake_up_returns_no_response_when_no_result(tmp_path: Path) -> None:
    state = {"topics": {}, "experiments": {}}
    messages = [FakeAssistantMessage(FakeTextBlock("partial"))]

    agent, _ = _make_client(state=state, project_root=tmp_path, messages=messages)

    import asyncio

    status = asyncio.run(agent.wake_up("silence"))

    assert status == "no_response"
    assert state["last_wakeup_status"] == "no_response"


def test_wake_up_invokes_on_event_callback_for_text_and_result(tmp_path: Path) -> None:
    state = {"topics": {}, "experiments": {}}
    messages = [
        FakeAssistantMessage(FakeTextBlock("thinking..."), FakeTextBlock("done")),
        FakeResultMessage(session_id="sid", is_error=False),
    ]

    agent, _ = _make_client(state=state, project_root=tmp_path, messages=messages)
    events: list[dict[str, Any]] = []

    import asyncio

    asyncio.run(agent.wake_up("check", on_event=events.append))

    text_events = [e for e in events if e["type"] == "text"]
    result_events = [e for e in events if e["type"] == "result"]
    assert [e["content"] for e in text_events] == ["thinking...", "done"]
    assert result_events and result_events[0]["session_id"] == "sid"


# --- disconnect --------------------------------------------------------------


def test_disconnect_is_idempotent_and_calls_sdk(tmp_path: Path) -> None:
    state = {"topics": {}, "experiments": {}}

    agent, fake = _make_client(state=state, project_root=tmp_path)

    import asyncio

    asyncio.run(agent.connect())
    asyncio.run(agent.disconnect())
    asyncio.run(agent.disconnect())  # second call must be safe

    assert fake.disconnect_calls == 1


def test_disconnect_swallows_sdk_errors(tmp_path: Path) -> None:
    state = {"topics": {}, "experiments": {}}

    agent, fake = _make_client(state=state, project_root=tmp_path)

    import asyncio

    asyncio.run(agent.connect())

    async def _raise() -> None:
        raise RuntimeError("boom")

    fake.disconnect = _raise  # type: ignore[assignment]

    asyncio.run(agent.disconnect())  # must not raise


def test_disconnect_without_connect_is_noop(tmp_path: Path) -> None:
    state = {"topics": {}, "experiments": {}}

    agent, _ = _make_client(state=state, project_root=tmp_path)

    import asyncio

    asyncio.run(agent.disconnect())  # never connected → no error


# --- make_wakeup_prompt ------------------------------------------------------


def test_make_wakeup_prompt_lists_every_pending_kind() -> None:
    todos = {
        "pending_replies": [{"id": "1"}, {"id": "2"}],
        "pending_topic_replies": [{"id": "x"}],
        "pending_reviews": [{"id": "r"}],
        "my_open_experiments": [{"id": "e1"}],
        "my_open_topics": [{"id": "t1"}],
        "mentions": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}],
    }

    prompt = make_wakeup_prompt("host", todos)

    assert "host" in prompt
    assert "map --persona host todos" in prompt
    assert "2 pending review reply(ies)" in prompt
    assert "1 pending topic reply(ies)" in prompt
    assert "1 pending review(s)" in prompt
    assert "1 open experiment(s)" in prompt
    assert "1 open topic(s)" in prompt
    assert "3 mention(s)" in prompt
    assert "topic-host" in prompt  # skill hint


def test_make_wakeup_prompt_reports_idle_when_empty() -> None:
    todos = {
        "pending_replies": [],
        "pending_topic_replies": [],
        "pending_reviews": [],
        "my_open_experiments": [],
        "my_open_topics": [],
        "mentions": [],
    }

    prompt = make_wakeup_prompt("participant", todos)

    assert "no pending items" in prompt


def test_make_wakeup_prompt_handles_missing_keys() -> None:
    prompt = make_wakeup_prompt("reviewer", {})

    assert "no pending items" in prompt
    assert "reviewer" in prompt
