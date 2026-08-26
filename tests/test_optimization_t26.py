"""T26：--creator / --executor 名称查找走同一 _resolve_agent_ref。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
import typer
from map_types.enums import AgentRole
from map_types.schemas.agent import AgentRead

from cli.runner import (
    _resolve_agent_ref,
    _resolve_creator_agent_id,
    _resolve_executor_agent_id,
)


def _agent(name: str, project_id: uuid.UUID, *, role: AgentRole = AgentRole.agent) -> AgentRead:
    return AgentRead(
        id=uuid.uuid4(),
        name=name,
        role=role,
        project_id=project_id,
        created_at=datetime.now(timezone.utc),
    )


def test_resolve_agent_ref_uuid_skips_list() -> None:
    client = MagicMock()
    pid = uuid.uuid4()
    target = uuid.uuid4()
    assert (
        _resolve_agent_ref(client, pid, str(target), flag="--executor", label="executor")
        == target
    )
    client.list_agents.assert_not_called()


def test_resolve_agent_ref_name_exact_match() -> None:
    pid = uuid.uuid4()
    host = _agent("proj-host", pid)
    client = MagicMock()
    client.list_agents.return_value = [host]
    assert (
        _resolve_agent_ref(client, pid, "proj-host", flag="--executor", label="executor")
        == host.id
    )


def test_resolve_agent_ref_zero_hits_exits(capsys: pytest.CaptureFixture[str]) -> None:
    pid = uuid.uuid4()
    client = MagicMock()
    client.list_agents.return_value = [_agent("proj-host", pid)]
    with pytest.raises(typer.Exit) as exc:
        _resolve_agent_ref(client, pid, "missing", flag="--executor", label="executor")
    assert exc.value.exit_code == 1
    assert "executor 'missing' not found" in capsys.readouterr().err


def test_resolve_agent_ref_ambiguous_uses_flag_hint(capsys: pytest.CaptureFixture[str]) -> None:
    pid = uuid.uuid4()
    a = _agent("dup", pid)
    b = _agent("dup", pid)
    client = MagicMock()
    client.list_agents.return_value = [a, b]
    with pytest.raises(typer.Exit) as exc:
        _resolve_agent_ref(
            client, pid, "dup", flag="--creator-agent-id", label="agent_name"
        )
    assert exc.value.exit_code == 1
    err = capsys.readouterr().err
    assert "agent_name 'dup' matches 2" in err
    assert "Pass --creator-agent-id <UUID>" in err


def test_creator_and_executor_share_name_lookup() -> None:
    pid = uuid.uuid4()
    host = _agent("proj-host", pid)
    client = MagicMock()
    client.list_agents.return_value = [host]
    assert _resolve_creator_agent_id(client, pid, "proj-host", None) == host.id
    assert _resolve_executor_agent_id(client, pid, "proj-host") == host.id
