"""Tests for ReviewerWorker with the in-process Claude SDK backend.

These exercise only ``run_forever`` with ``agent_backend="claude-agent"``;
the legacy ``subprocess`` path was retired in v0.7 P4 along with the
reviewer lifecycle mixin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cli.agent_client import make_wakeup_prompt
from cli.host_worker_types import WorkerError
from cli.reviewer_worker import (
    ReviewerMapClient,
    ReviewerWorker,
    ReviewerWorkerConfig,
    ReviewerWorkerStats,
    VALID_AGENT_BACKENDS,
)


class FakePersonaAgentClient:
    def __init__(
        self,
        *,
        wake_status: str = "ok",
        wake_session_id: str | None = "session-xyz",
        raise_on_wake: Exception | None = None,
        raise_on_connect: Exception | None = None,
    ) -> None:
        self.state: dict[str, Any] = {}
        self.wake_status = wake_status
        self.wake_session_id = wake_session_id
        self.raise_on_wake = raise_on_wake
        self.raise_on_connect = raise_on_connect
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.wake_calls: list[str] = []
        self._connected = False

    async def connect(self) -> None:
        if self._connected:
            return
        self._connected = True
        self.connect_calls += 1
        if self.raise_on_connect is not None:
            self._connected = False
            raise self.raise_on_connect

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._connected = False

    async def wake_up(self, prompt: str, *, on_event: Any = None) -> str:
        self.wake_calls.append(prompt)
        if self.raise_on_wake is not None:
            raise self.raise_on_wake
        if self.wake_session_id:
            self.state["claude_session_id"] = self.wake_session_id
        return self.wake_status


class FakeReviewerClient(ReviewerMapClient):
    def __init__(self, *, agent_id: str = "reviewer-agent", todos: dict[str, Any] | None = None) -> None:
        self._agent_id = agent_id
        self._todos = todos or {}
        self.whoami_calls = 0
        self.todos_calls = 0

    def whoami(self) -> dict[str, Any]:
        self.whoami_calls += 1
        return {"id": self._agent_id, "name": "reviewer"}

    def todos(self) -> dict[str, Any]:
        self.todos_calls += 1
        return self._todos


# --- dispatch / config ------------------------------------------------------


def test_valid_agent_backends_constant():
    assert VALID_AGENT_BACKENDS == ("claude-agent",)


def test_unknown_agent_backend_raises(tmp_path: Path):
    client = FakeReviewerClient()
    with pytest.raises(WorkerError, match="Unknown agent_backend"):
        ReviewerWorker(
            client,  # type: ignore[arg-type]
            ReviewerWorkerConfig(once=True, agent_backend="bogus", state_file=tmp_path / "s.json"),
        )


def test_subprocess_backend_raises():
    client = FakeReviewerClient()
    with pytest.raises(WorkerError, match="Unknown agent_backend"):
        ReviewerWorker(
            client,  # type: ignore[arg-type]
            ReviewerWorkerConfig(once=True, agent_backend="subprocess"),
        )


# --- claude-agent backend ----------------------------------------------------


def _make_worker(
    todos: dict[str, Any],
    tmp_path: Path,
    *,
    agent: FakePersonaAgentClient,
    once: bool = True,
) -> ReviewerWorker:
    client = FakeReviewerClient(todos=todos)
    config = ReviewerWorkerConfig(
        once=once,
        agent_backend="claude-agent",
        state_file=tmp_path / "state.json",
    )
    return ReviewerWorker(client, config, agent_client=agent)  # type: ignore[arg-type]


def test_claude_agent_backend_connects_then_wakes_up_once(tmp_path: Path):
    todos = {
        "pending_reviews": [{"id": "exp-1", "current_plan_version": 1}],
    }
    agent = FakePersonaAgentClient(wake_status="ok", wake_session_id="sess-1")

    stats = _make_worker(todos, tmp_path, agent=agent).run_forever()

    assert agent.connect_calls == 1
    assert agent.disconnect_calls == 1
    assert agent.wake_calls == [make_wakeup_prompt("reviewer", todos)]
    assert stats.cycles == 1
    assert stats.runner_invocations == 1


def test_claude_agent_backend_wake_up_error_is_counted(tmp_path: Path):
    agent = FakePersonaAgentClient(wake_status="error", wake_session_id="sess-err")

    stats = _make_worker({}, tmp_path, agent=agent).run_forever()

    assert stats.runner_invocations == 1
    assert agent.disconnect_calls == 1


def test_claude_agent_backend_wake_up_exception_does_not_crash(tmp_path: Path):
    agent = FakePersonaAgentClient(raise_on_wake=RuntimeError("boom"))

    stats = _make_worker({}, tmp_path, agent=agent).run_forever()

    assert stats.runner_errors == 1
    assert stats.runner_invocations == 0
    assert agent.disconnect_calls == 1


def test_claude_agent_backend_connect_failure_propagates(tmp_path: Path):
    agent = FakePersonaAgentClient(raise_on_connect=RuntimeError("connect failed"))

    with pytest.raises(RuntimeError, match="connect failed"):
        _make_worker({}, tmp_path, agent=agent).run_forever()

    assert agent.disconnect_calls == 0


def test_claude_agent_backend_passes_todos_into_wakeup_prompt(tmp_path: Path):
    todos = {
        "pending_reviews": [
            {"id": "exp-1"},
            {"id": "exp-2"},
        ],
    }
    agent = FakePersonaAgentClient()

    _make_worker(todos, tmp_path, agent=agent).run_forever()

    prompt = agent.wake_calls[0]
    assert "2 pending review(s)" in prompt
    assert "map --persona reviewer todos" in prompt


def test_claude_agent_backend_idle_prompt_when_no_todos(tmp_path: Path):
    agent = FakePersonaAgentClient()

    _make_worker({}, tmp_path, agent=agent).run_forever()

    prompt = agent.wake_calls[0]
    assert "no pending items" in prompt


def test_claude_agent_backend_keeps_existing_session_id_in_state(tmp_path: Path):
    """If state already has claude_session_id, the wakeup prompt is sent regardless;
    the agent client (real or fake) decides whether to resume.
    """
    state_file = tmp_path / "state.json"
    state_file.write_text(
        '{"schema_version":1,"claude_session_id":"prior","experiments":{},"resolved_items":[]}',
        encoding="utf-8",
    )
    agent = FakePersonaAgentClient(wake_session_id="prior")

    client = FakeReviewerClient()
    ReviewerWorker(
        client,  # type: ignore[arg-type]
        ReviewerWorkerConfig(once=True, agent_backend="claude-agent", state_file=state_file),
        agent_client=agent,  # type: ignore[arg-type]
    ).run_forever()

    assert agent.wake_calls  # prompted exactly once


def test_make_wakeup_prompt_for_reviewer_skill_reference():
    prompt = make_wakeup_prompt("reviewer", {"pending_reviews": [{"id": "1"}]})
    assert "topic-reviewer" in prompt
    assert ".cursor/skills/" in prompt


def test_reviewer_worker_idle_when_no_agent(tmp_path: Path):
    """A worker built without an injected agent still produces valid stats
    (initialised from whoami + zero cycles).
    """
    client = FakeReviewerClient()
    config = ReviewerWorkerConfig(once=True, agent_backend="claude-agent", state_file=tmp_path / "s.json")

    worker = ReviewerWorker(client, config)  # type: ignore[arg-type]
    stats = ReviewerWorkerStats()
    assert stats.cycles == 0
    assert stats.reviews_created == 0
    assert stats.items_resolved == 0
    assert worker.config.agent_backend == "claude-agent"
