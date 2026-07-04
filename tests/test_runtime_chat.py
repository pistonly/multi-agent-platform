"""Tests for cli.runtime_chat."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import typer

from cli.agent_client import PersonaAgentClient, WakeUpEvent
from cli.host_worker_types import WorkerError
from cli.runtime_chat import (
    default_runtime_home,
    default_state_file,
    dump_runtime_chat_status,
    ensure_waker_not_running,
    format_session_banner,
    load_runtime_state,
    persona_agent_state,
    resolve_session_id,
    run_chat_loop,
    run_runtime_chat,
)


def test_default_paths_use_persona_suffix(tmp_path: Path) -> None:
    assert default_state_file(tmp_path, "host") == tmp_path / ".map/runtime-waker-state-host.json"
    assert default_runtime_home(tmp_path, "reviewer") == tmp_path / ".map/claude-runtime-home-reviewer"


def test_resolve_session_id_prefers_explicit_override() -> None:
    state = {"claude_session_id": "from-state", "runtime_session_id": "alt"}
    assert resolve_session_id(state, session_id="explicit", new_session=False) == "explicit"


def test_resolve_session_id_new_session_returns_none() -> None:
    state = {"claude_session_id": "from-state"}
    assert resolve_session_id(state, session_id=None, new_session=True) is None


def test_resolve_session_id_reads_runtime_session_id() -> None:
    state = {"runtime_session_id": "runtime-only"}
    assert resolve_session_id(state, session_id=None, new_session=False) == "runtime-only"


def test_load_runtime_state_creates_personas_bucket(tmp_path: Path) -> None:
    state = load_runtime_state(tmp_path / "missing.json")
    assert state["schema_version"] == 1
    assert state["personas"] == {}


def test_persona_agent_state_is_nested_dict(tmp_path: Path) -> None:
    state = load_runtime_state(tmp_path / "state.json")
    host = persona_agent_state(state, "host")
    host["claude_session_id"] = "abc"
    assert state["personas"]["host"]["claude_session_id"] == "abc"


def test_format_session_banner_includes_resume_mode() -> None:
    banner = format_session_banner(
        persona="host",
        session_id="sess-1",
        state_file=Path("/tmp/state.json"),
        runtime_home=Path("/tmp/home"),
        resumed=True,
    )
    assert "persona=host" in banner
    assert "resume" in banner
    assert "sess-1" in banner


def test_ensure_waker_not_running_raises_when_pgrep_finds_pids() -> None:
    with patch("cli.runtime_chat.find_runtime_waker_pids", return_value=[12345]):
        with pytest.raises(WorkerError, match="runtime waker"):
            ensure_waker_not_running(persona="host", ignore_waker=False)


def test_ensure_waker_not_running_ignored_with_flag() -> None:
    with patch("cli.runtime_chat.find_runtime_waker_pids", return_value=[12345]):
        ensure_waker_not_running(persona="host", ignore_waker=True)


def test_dump_runtime_chat_status_reads_state_file(tmp_path: Path) -> None:
    state_file = default_state_file(tmp_path, "host")
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        '{"schema_version":1,"personas":{"host":{"claude_session_id":"sess-xyz"}}}\n',
        encoding="utf-8",
    )
    with patch("cli.runtime_chat.find_runtime_waker_pids", return_value=[]):
        status = dump_runtime_chat_status(persona="host", project_root=tmp_path, state_file=None)
    assert status["session_id"] == "sess-xyz"
    assert status["waker_pids"] == []


class _FakeChatClient:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"claude_session_id": "sess-1"}
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.prompts: list[str] = []

    async def connect(self) -> None:
        self.connect_calls += 1

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def wake_up(
        self,
        prompt: str,
        *,
        on_event: Any = None,
        event_source: str = "polling",
    ) -> str:
        self.prompts.append(prompt)
        if on_event is not None:
            on_event({"type": "text", "content": f"echo:{prompt}"})
        assert event_source == "manual"
        return "ok"


@pytest.mark.asyncio
async def test_run_chat_loop_handles_slash_commands() -> None:
    fake = _FakeChatClient()
    inputs = iter(["/session", "/help", "continue I5", "/quit"])

    def fake_input(_prompt: str) -> str:
        return next(inputs)

    captured: list[WakeUpEvent] = []

    def on_event(event: WakeUpEvent) -> None:
        captured.append(event)

    await run_chat_loop(fake, initial_prompt=None, input_fn=fake_input, on_event=on_event)

    assert fake.connect_calls == 1
    assert fake.disconnect_calls == 1
    assert fake.prompts == ["continue I5"]
    assert captured and captured[0]["content"] == "echo:continue I5"


def test_run_runtime_chat_exits_on_worker_error() -> None:
    with patch(
        "cli.runtime_chat.run_runtime_chat_async",
        side_effect=WorkerError("blocked"),
    ):
        with pytest.raises(typer.Exit) as exc:
            run_runtime_chat(
                persona="host",
                project_root=None,
                state_file=None,
                runtime_home=None,
                session_id=None,
                new_session=False,
                initial_prompt=None,
                ignore_waker=False,
                model=None,
            )
        assert exc.value.exit_code == 1


def test_manual_integration_system_prompt(tmp_path: Path) -> None:
    client = PersonaAgentClient(
        persona="host",
        state={},
        save_state_fn=lambda: None,
        project_root=tmp_path,
        integration="manual",
    )
    prompt = client._system_append_prompt()
    assert "map runtime chat" in prompt
    assert "operator" in prompt.lower()
