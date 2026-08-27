"""Unit tests for the Cursor SDK simple-waker backend (no real cursor-sdk)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cli.cursor_wake_backend import (
    DEFAULT_CURSOR_MODEL,
    CursorSdkWakeBackend,
    apply_project_cursor_env,
    load_project_cursor_env,
)
from cli.errors import WorkerError
from cli.wake_backend import (
    PersonaAgentWakeBackend,
    build_wake_backend,
    resolve_waker_runtime,
)


class FakeResult:
    def __init__(self, *, status: str = "finished", result: str = "done", id: str = "run-1") -> None:
        self.status = status
        self.result = result
        self.id = id


class FakeRun:
    def __init__(
        self,
        result: FakeResult | None = None,
        messages: list[Any] | None = None,
        *,
        wait_sleep: float = 0.0,
    ) -> None:
        self._result = result or FakeResult()
        self._messages = list(messages or [])
        self.wait_sleep = wait_sleep
        self.cancelled = False
        self.wait_calls = 0

    async def wait(self) -> FakeResult:
        self.wait_calls += 1
        if self.wait_sleep:
            await asyncio.sleep(self.wait_sleep)
        return self._result

    async def messages(self):
        for message in self._messages:
            yield message

    def supports(self, op: str) -> bool:
        return op == "cancel"

    async def cancel(self) -> None:
        self.cancelled = True


class FakeAgent:
    def __init__(self, agent_id: str = "agent-test-1") -> None:
        self.agent_id = agent_id
        self.prompts: list[tuple[str, Any]] = []
        self.closed = False
        self.run = FakeRun()

    async def send(self, prompt: str, options: Any = None) -> FakeRun:
        self.prompts.append((prompt, options))
        return self.run

    async def close(self) -> None:
        self.closed = True


class FakeAgents:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.resume_calls: list[tuple[str, dict[str, Any]]] = []
        self.agent = FakeAgent()
        self.resume_error: Exception | None = None

    async def create(self, **kwargs: Any) -> FakeAgent:
        self.create_calls.append(kwargs)
        return self.agent

    async def resume(self, agent_id: str, options: Any = None, **kwargs: Any) -> FakeAgent:
        self.resume_calls.append((agent_id, options if options is not None else kwargs))
        if self.resume_error is not None:
            raise self.resume_error
        self.agent.agent_id = agent_id
        return self.agent


class FakeClient:
    def __init__(self) -> None:
        self.agents = FakeAgents()
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _backend(
    tmp_path: Path,
    *,
    state: dict[str, Any] | None = None,
    client: FakeClient | None = None,
    model: str | None = None,
    wake_timeout: float = 1800.0,
) -> tuple[CursorSdkWakeBackend, FakeClient, dict[str, Any]]:
    fake = client or FakeClient()
    agent_state: dict[str, Any] = {} if state is None else state

    def save() -> None:
        agent_state["_saved"] = True

    backend = CursorSdkWakeBackend(
        project_root=tmp_path,
        persona="host",
        get_agent_state=lambda: agent_state,
        save_state_fn=save,
        model=model,
        wake_timeout=wake_timeout,
        session_log_dir=tmp_path / "session-logs",
        _launch_bridge=lambda _workspace: fake,
    )
    return backend, fake, agent_state


@pytest.fixture
def cursor_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    monkeypatch.delenv("CURSOR_MODEL", raising=False)


def test_resolve_waker_runtime_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAP_SIMPLE_RUNTIME", raising=False)
    assert resolve_waker_runtime(None) == "claude"
    monkeypatch.setenv("MAP_SIMPLE_RUNTIME", "cursor")
    assert resolve_waker_runtime(None) == "cursor"
    assert resolve_waker_runtime("claude") == "claude"
    with pytest.raises(WorkerError, match="Unknown waker runtime"):
        resolve_waker_runtime("codex")


def test_build_wake_backend_selects_cursor(tmp_path: Path) -> None:
    state: dict[str, Any] = {}
    backend = build_wake_backend(
        runtime="cursor",
        project_root=tmp_path,
        persona="host",
        get_agent_state=lambda: state,
        save_state_fn=lambda: None,
    )
    assert isinstance(backend, CursorSdkWakeBackend)


def test_build_wake_backend_selects_claude(tmp_path: Path) -> None:
    state: dict[str, Any] = {}
    backend = build_wake_backend(
        runtime="claude",
        project_root=tmp_path,
        persona="host",
        get_agent_state=lambda: state,
        save_state_fn=lambda: None,
    )
    assert isinstance(backend, PersonaAgentWakeBackend)


def test_apply_project_cursor_env_overrides_and_unsets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_API_KEY", "legacy-key")
    assert apply_project_cursor_env(tmp_path) == {}
    assert os.environ["CURSOR_API_KEY"] == "legacy-key"

    env_dir = tmp_path / ".map"
    env_dir.mkdir()
    (env_dir / ".cursor-env").write_text(
        "export CURSOR_API_KEY=file-key\nexport CURSOR_MODEL=composer-2.5\n"
    )
    monkeypatch.setenv("CURSOR_API_KEY", "shell-key")
    monkeypatch.setenv("CURSOR_MODEL", "legacy-model")
    monkeypatch.setenv("UNRELATED", "keep")

    applied = apply_project_cursor_env(tmp_path)
    assert applied["CURSOR_API_KEY"] == "file-key"
    assert os.environ["CURSOR_API_KEY"] == "file-key"
    assert os.environ["CURSOR_MODEL"] == "composer-2.5"
    assert os.environ["UNRELATED"] == "keep"


def test_apply_project_cursor_env_unsets_missing_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_dir = tmp_path / ".map"
    env_dir.mkdir()
    (env_dir / ".cursor-env").write_text("export CURSOR_API_KEY=file-key\n")
    monkeypatch.setenv("CURSOR_MODEL", "shell-model")
    apply_project_cursor_env(tmp_path)
    assert os.environ["CURSOR_API_KEY"] == "file-key"
    assert "CURSOR_MODEL" not in os.environ


def test_load_project_cursor_env_missing_file(tmp_path: Path) -> None:
    assert load_project_cursor_env(tmp_path) == {}


@pytest.mark.asyncio
async def test_connect_creates_local_agent(tmp_path: Path, cursor_key: None) -> None:
    backend, fake, state = _backend(tmp_path)
    await backend.connect()
    try:
        assert len(fake.agents.create_calls) == 1
        kwargs = fake.agents.create_calls[0]
        assert kwargs["api_key"] == "test-key"
        assert kwargs["model"] == DEFAULT_CURSOR_MODEL
        assert kwargs["local"]["cwd"] == str(tmp_path)
        assert kwargs["local"]["setting_sources"] == ["project"]
        assert fake.agents.resume_calls == []
        assert state["runtime_backend"] == "cursor"
        assert state["cursor_agent_id"] == "agent-test-1"
        assert state["runtime_session_id"] == "agent-test-1"
    finally:
        await backend.disconnect()
        assert fake.closed is True
        assert fake.agents.agent.closed is True


@pytest.mark.asyncio
async def test_connect_resumes_cursor_agent_id(tmp_path: Path, cursor_key: None) -> None:
    backend, fake, _state = _backend(
        tmp_path,
        state={"runtime_backend": "cursor", "cursor_agent_id": "agent-old"},
    )
    await backend.connect()
    try:
        assert fake.agents.create_calls == []
        assert fake.agents.resume_calls[0][0] == "agent-old"
        assert fake.agents.resume_calls[0][1]["local"]["setting_sources"] == ["project"]
        assert fake.agents.agent.agent_id == "agent-old"
    finally:
        await backend.disconnect()


@pytest.mark.asyncio
async def test_connect_ignores_claude_session_ids(tmp_path: Path, cursor_key: None) -> None:
    backend, fake, _state = _backend(
        tmp_path,
        state={
            "runtime_backend": "claude",
            "claude_session_id": "claude-sess",
            "runtime_session_id": "claude-sess",
        },
    )
    await backend.connect()
    try:
        assert fake.agents.resume_calls == []
        assert len(fake.agents.create_calls) == 1
    finally:
        await backend.disconnect()


@pytest.mark.asyncio
async def test_connect_resume_failure_creates_new_agent(tmp_path: Path, cursor_key: None) -> None:
    fake = FakeClient()
    fake.agents.resume_error = RuntimeError("agent gone")
    backend, fake, state = _backend(
        tmp_path,
        state={"runtime_backend": "cursor", "cursor_agent_id": "agent-old"},
        client=fake,
    )
    await backend.connect()
    try:
        assert fake.agents.resume_calls[0][0] == "agent-old"
        assert len(fake.agents.create_calls) == 1
        assert state["cursor_agent_id"] == "agent-test-1"
    finally:
        await backend.disconnect()


@pytest.mark.asyncio
async def test_connect_requires_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    backend, _fake, _state = _backend(tmp_path)
    with pytest.raises(WorkerError, match="CURSOR_API_KEY"):
        await backend.connect()


@pytest.mark.asyncio
async def test_wake_async_sends_prompt_and_returns_text(tmp_path: Path, cursor_key: None) -> None:
    backend, fake, _state = _backend(tmp_path)
    fake.agents.agent.run = FakeRun(
        FakeResult(result="from-result"),
        messages=[
            SimpleNamespace(
                type="assistant",
                message=SimpleNamespace(content=[SimpleNamespace(type="text", text="hello from cursor")]),
            )
        ],
    )
    result = await backend.wake_async(prompt="do the work")
    try:
        assert result.session_id == "agent-test-1"
        assert result.response_text == "hello from cursor"
        assert fake.agents.agent.prompts[0][0] == "do the work"
        assert fake.agents.agent.run.wait_calls == 1
    finally:
        await backend.disconnect()


@pytest.mark.asyncio
async def test_wake_async_error_status_raises(tmp_path: Path, cursor_key: None) -> None:
    backend, fake, _state = _backend(tmp_path)
    fake.agents.agent.run = FakeRun(FakeResult(status="error"))
    with pytest.raises(WorkerError, match="status='error'"):
        await backend.wake_async(prompt="do the work")
    await backend.disconnect()


@pytest.mark.asyncio
async def test_wake_timeout_cancels_and_disconnects(tmp_path: Path, cursor_key: None) -> None:
    backend, fake, _state = _backend(tmp_path, wake_timeout=0.05)
    fake.agents.agent.run = FakeRun(wait_sleep=1.0)
    with pytest.raises(WorkerError, match="timed out"):
        await backend.wake_async(prompt="do the work")
    assert fake.agents.agent.run.cancelled is True
    assert backend._connected is False
    assert fake.closed is True


@pytest.mark.asyncio
async def test_reset_session_clears_cursor_ids(tmp_path: Path, cursor_key: None) -> None:
    state = {
        "runtime_backend": "cursor",
        "cursor_agent_id": "agent-old",
        "runtime_session_id": "agent-old",
        "claude_session_id": "should-go",
    }
    backend, fake, state = _backend(tmp_path, state=state)
    await backend.connect()
    await backend.reset_session()
    assert "cursor_agent_id" not in state
    assert "runtime_session_id" not in state
    assert "runtime_backend" not in state
    assert fake.closed is True


@pytest.mark.asyncio
async def test_model_override_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    monkeypatch.setenv("CURSOR_MODEL", "env-model")
    backend, fake, _state = _backend(tmp_path, model="flag-model")
    await backend.connect()
    try:
        assert fake.agents.create_calls[0]["model"] == "flag-model"
    finally:
        await backend.disconnect()

    backend, fake, _state = _backend(tmp_path)
    await backend.connect()
    try:
        assert fake.agents.create_calls[0]["model"] == "env-model"
    finally:
        await backend.disconnect()
