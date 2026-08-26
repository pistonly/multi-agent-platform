"""Tests for cli.agent_client — persistent in-process Claude SDK client."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

# Collection-time guard: skip entire module if claude_agent_sdk is not
# installed, so a clean `pip install -e ".[dev]"` + `pytest` run does not
# fail with ModuleNotFoundError. The claude_runtime marker + conftest
# autouse fixture (skip_claude_runtime_if_unavailable) handle the finer-
# grained "installed but don't run" case.
pytest.importorskip("claude_agent_sdk")

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from cli.agent_client import PersonaAgentClient, make_wakeup_prompt
from cli.session_wake_log import resolve_session_log_path

pytestmark = pytest.mark.claude_runtime

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


class FakeToolUseBlock(ToolUseBlock):
    def __init__(self, *, id: str, name: str, input: dict | None = None) -> None:
        super().__init__(id=id, name=name, input=input or {})


class FakeToolResultBlock(ToolResultBlock):
    def __init__(
        self, *, tool_use_id: str, content: Any = "ok", is_error: bool | None = None
    ) -> None:
        super().__init__(tool_use_id=tool_use_id, content=content, is_error=is_error)


class FakeUserMessage(UserMessage):
    def __init__(self, *blocks: Any) -> None:
        super().__init__(content=list(blocks))


class FakeReceive:
    """Async iterator of canned messages passed to receive_response()."""

    def __init__(self, messages: list[Any]) -> None:
        self._messages = list(messages)

    def __aiter__(self) -> FakeReceive:
        self._iter = iter(self._messages)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


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
    wake_timeout: float | None = None,
) -> tuple[PersonaAgentClient, FakeClaudeClient]:
    fake = FakeClaudeClient(messages=messages)
    save_calls: list[int] = []

    def save() -> None:
        save_calls.append(1)

    agent_kwargs: dict[str, Any] = dict(
        persona="host",
        state=state,
        save_state_fn=save,
        project_root=project_root,
        extra_env={},
        model="claude-test-model",
        _client_factory=lambda options: _capture_then_return(options, fake),
    )
    if wake_timeout is not None:
        agent_kwargs["wake_timeout"] = wake_timeout
    agent = PersonaAgentClient(**agent_kwargs)
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


def test_wake_up_logs_tool_use_and_result_events(tmp_path: Path) -> None:
    """Live event stream: each assistant text / tool call / tool result is
    appended to the session jsonl, so a stuck agent is visible from the
    timestamp of the last written event."""
    state = {"topics": {}, "experiments": {}, "claude_session_id": "sess-t"}
    log_dir = tmp_path / "session-logs"
    messages = [
        FakeAssistantMessage(
            FakeTextBlock("let me check"),
            FakeToolUseBlock(id="tu-1", name="Bash", input={"command": "map todos"}),
        ),
        FakeUserMessage(FakeToolResultBlock(tool_use_id="tu-1", content="no pending items")),
        FakeAssistantMessage(FakeTextBlock("done")),
        FakeResultMessage(session_id="sess-t", is_error=False),
    ]

    agent, _ = _make_client(state=state, project_root=tmp_path, messages=messages)
    agent.session_log_dir = log_dir

    import asyncio
    import json

    events: list[dict[str, Any]] = []
    asyncio.run(agent.wake_up("check todos", on_event=events.append))

    log_path = resolve_session_log_path(log_dir, "sess-t", "host")
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").strip().splitlines()]
    event_seq = [e.get("event") for e in entries if "event" in e]
    assert event_seq == ["wake", "text", "tool_use", "tool_result", "text"]

    tool_use_entry = next(e for e in entries if e.get("event") == "tool_use")
    assert "Bash" in tool_use_entry["summary"]
    assert "map todos" in tool_use_entry["summary"]

    tool_result_entry = next(e for e in entries if e.get("event") == "tool_result")
    assert tool_result_entry["summary"].startswith("ok:")
    assert "no pending items" in tool_result_entry["summary"]

    # on_event mirrors the same sequence (text/tool_use/tool_result/text/result).
    assert [e["type"] for e in events] == ["text", "tool_use", "tool_result", "text", "result"]

    # The wake still ends with a result summary entry carrying status.
    result_entry = next(e for e in entries if "status" in e)
    assert result_entry["status"] == "ok"


def test_wake_up_writes_session_log_with_prompt_and_response_preview(tmp_path: Path) -> None:
    state = {"topics": {}, "experiments": {}, "claude_session_id": "sess-abc"}
    long_reply = "x" * 250
    messages = [
        FakeAssistantMessage(FakeTextBlock(long_reply)),
        FakeResultMessage(session_id="sess-abc", is_error=False),
    ]
    log_dir = tmp_path / "session-logs"

    agent, _ = _make_client(state=state, project_root=tmp_path, messages=messages)
    agent.session_log_dir = log_dir

    import asyncio

    asyncio.run(agent.wake_up("wake prompt body"))

    log_path = resolve_session_log_path(log_dir, "sess-abc", "host")
    assert log_path.is_file()
    import json

    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").strip().splitlines()]
    # A wake now writes a live event stream (wake/text/...) plus the result
    # summary entry; assert both are present.
    events = [e.get("event") for e in entries]
    assert "wake" in events and "text" in events
    entry = next(e for e in entries if "status" in e)
    assert entry["session_id"] == "sess-abc"
    assert entry["persona"] == "host"
    assert entry["prompt"] == "wake prompt body"
    assert entry["response_preview"] == "x" * 200
    assert entry["response_chars"] == 250
    assert entry["status"] == "ok"


def test_wake_up_appends_multiple_entries_for_same_session(tmp_path: Path) -> None:
    state = {"topics": {}, "experiments": {}, "claude_session_id": "sess-repeat"}
    log_dir = tmp_path / "session-logs"

    agent, fake = _make_client(
        state=state,
        project_root=tmp_path,
        messages=[FakeResultMessage(session_id="sess-repeat", is_error=False)],
    )
    agent.session_log_dir = log_dir

    import asyncio

    asyncio.run(agent.wake_up("first"))
    fake.messages = [
        FakeAssistantMessage(FakeTextBlock("second reply")),
        FakeResultMessage(session_id="sess-repeat", is_error=False),
    ]
    asyncio.run(agent.wake_up("second"))

    import json

    entries = [
        json.loads(line)
        for line in resolve_session_log_path(log_dir, "sess-repeat", "host")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    ]
    # Each wake appends its own result summary entry to the same file.
    result_entries = [e for e in entries if "status" in e]
    assert len(result_entries) == 2


def test_wake_up_passes_d5_join_keys_to_session_log(tmp_path: Path) -> None:
    """D5: wake_up forwards event_id / event_source / fingerprint to the jsonl.

    Without this, the waker's `WakeEvent.event_id` / fingerprint cannot reach
    the sessions jsonl, leaving A3 (notification ↔ inbound_event ↔ jsonl)
    without a join key.
    """
    state = {"topics": {}, "experiments": {}, "claude_session_id": "sid-jk"}
    log_dir = tmp_path / "session-logs"
    messages = [FakeResultMessage(session_id="sid-jk", is_error=False)]

    agent, _ = _make_client(state=state, project_root=tmp_path, messages=messages)
    agent.session_log_dir = log_dir

    import asyncio
    import json

    asyncio.run(
        agent.wake_up(
            "wake",
            event_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            event_source="polling",
            fingerprint="host:pending_topic_reply:topic-1:comment-1",
        )
    )

    entries = [
        json.loads(line)
        for line in resolve_session_log_path(log_dir, "sid-jk", "host")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    ]
    # Join keys land on both the live events and the result summary entry.
    result_entry = next(e for e in entries if "status" in e)
    assert result_entry["event_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert result_entry["event_source"] == "polling"
    assert result_entry["fingerprint"] == "host:pending_topic_reply:topic-1:comment-1"
    wake_events = [e for e in entries if e.get("event") == "wake"]
    assert wake_events and wake_events[0]["event_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_wake_up_session_log_defaults_event_source_to_polling(tmp_path: Path) -> None:
    """Phase 1 default: polling is the only source until SSE lands in Phase 2."""
    state = {"topics": {}, "experiments": {}, "claude_session_id": "sid-d"}
    log_dir = tmp_path / "session-logs"
    messages = [FakeResultMessage(session_id="sid-d", is_error=False)]

    agent, _ = _make_client(state=state, project_root=tmp_path, messages=messages)
    agent.session_log_dir = log_dir

    import asyncio
    import json

    asyncio.run(agent.wake_up("wake"))

    entries = [
        json.loads(line)
        for line in resolve_session_log_path(log_dir, "sid-d", "host")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    ]
    result_entry = next(e for e in entries if "status" in e)
    assert result_entry["event_source"] == "polling"
    assert result_entry["event_id"] is None
    assert result_entry["fingerprint"] is None


# --- wake_timeout (P2 hang guard) -------------------------------------------


class HangingReceive:
    """Async iterator that blocks forever on each ``__anext__``.

    Drives ``wake_timeout``: production ``wake_up`` wraps each ``__anext__`` in
    ``asyncio.wait_for(..., timeout=wake_timeout)``, so an iterator that
    genuinely blocks is the only way to exercise that path.
    """

    def __init__(self, hang_for: float = 60.0) -> None:
        self._hang_for = hang_for

    def __aiter__(self) -> HangingReceive:
        return self

    async def __anext__(self) -> Any:
        await asyncio.sleep(self._hang_for)
        raise StopAsyncIteration  # unreachable when hang_for > wake_timeout


class PartialThenHangingReceive:
    """Async iterator that yields ``messages`` then blocks on the next call.

    Models the realistic failure mode: SDK emits a few messages then the
    agent process crashes mid-turn and stops emitting.
    """

    def __init__(self, messages: list[Any], hang_for: float = 60.0) -> None:
        self._messages = list(messages)
        self._hang_for = hang_for

    def __aiter__(self) -> PartialThenHangingReceive:
        self._iter = iter(self._messages)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._iter)
        except StopIteration as exc:
            await asyncio.sleep(self._hang_for)
            raise StopAsyncIteration from exc  # unreachable when hang_for > wake_timeout


def test_wake_up_aborts_with_no_response_when_receive_response_hangs(tmp_path: Path) -> None:
    """When the SDK never emits a message, wake_timeout must fire and the
    wake must end with status="no_response" rather than blocking the waker
    forever.
    """
    state = {"topics": {}, "experiments": {}}
    agent, fake = _make_client(
        state=state,
        project_root=tmp_path,
        messages=None,
        wake_timeout=0.1,
    )
    fake.receive_response = lambda: HangingReceive(hang_for=60.0)

    import asyncio
    import time

    start = time.monotonic()
    status = asyncio.run(agent.wake_up("stuck agent"))
    elapsed = time.monotonic() - start

    assert status == "no_response"
    assert state["last_wakeup_status"] == "no_response"
    # The 60s hang was not waited out — we returned shortly after wake_timeout.
    assert elapsed < 5.0, f"wake_up did not respect wake_timeout (elapsed={elapsed:.2f}s)"
    # query() was still called before the hang — only the receive side aborts.
    assert fake.query_calls == ["stuck agent"]
    # T22: timeout must disconnect so the next wake reconnects fresh instead of
    # reusing the client whose receive_response() stream is still hanging.
    assert fake.disconnect_calls == 1
    assert agent._connected is False
    assert agent._client is None


def test_wake_up_logs_timeout_event_when_receive_response_hangs(tmp_path: Path) -> None:
    """A timed-out wake leaves a ``timeout`` event in the session jsonl with
    the configured wake_timeout in the summary, so postmortem can distinguish
    'agent stopped emitting' from 'agent returned cleanly with no message'.
    """
    state = {"topics": {}, "experiments": {}, "claude_session_id": "sid-hang"}
    log_dir = tmp_path / "session-logs"
    agent, fake = _make_client(
        state=state,
        project_root=tmp_path,
        messages=None,
        wake_timeout=0.1,
    )
    fake.receive_response = lambda: HangingReceive(hang_for=60.0)
    agent.session_log_dir = log_dir

    import asyncio
    import json

    asyncio.run(agent.wake_up("stuck"))

    entries = [
        json.loads(line)
        for line in resolve_session_log_path(log_dir, "sid-hang", "host")
        .read_text(encoding="utf-8")
        .strip()
        .splitlines()
    ]
    events = [e.get("event") for e in entries if "event" in e]
    assert events[0] == "wake"
    assert "timeout" in events, f"expected timeout event, got {events}"
    timeout_entry = next(e for e in entries if e.get("event") == "timeout")
    assert "0.1" in timeout_entry["summary"]


def test_wake_up_aborts_when_messages_stop_mid_stream(tmp_path: Path) -> None:
    """Realistic failure mode: SDK emits some messages then goes silent
    (agent process crashes mid-turn). wake_timeout must still fire and the
    wake must end cleanly rather than block on the next ``__anext__``.
    """
    state = {"topics": {}, "experiments": {}}
    partial = [FakeAssistantMessage(FakeTextBlock("started then went silent"))]
    agent, fake = _make_client(
        state=state,
        project_root=tmp_path,
        messages=None,
        wake_timeout=0.1,
    )
    fake.receive_response = lambda: PartialThenHangingReceive(partial, hang_for=60.0)

    import asyncio
    import time

    events: list[dict[str, Any]] = []
    start = time.monotonic()
    status = asyncio.run(agent.wake_up("dies mid-stream", on_event=events.append))
    elapsed = time.monotonic() - start

    assert status == "no_response"
    # The text event landed before the hang; no result event after.
    assert [e["type"] for e in events] == ["text"]
    assert elapsed < 5.0, f"mid-stream hang was not aborted (elapsed={elapsed:.2f}s)"
    # T22: mid-stream hang also disconnects — next wake reconnects fresh.
    assert fake.disconnect_calls == 1
    assert agent._connected is False


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
