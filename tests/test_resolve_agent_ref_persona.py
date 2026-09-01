"""Tests for plan-mode-direct-execution-productization Blocker A: persona
short-name resolution in ``_resolve_agent_ref``.

Contract:
- ``host`` / ``participant`` / ``reviewer`` are persona short names. They
  resolve via ``map_types.persona.pick_agent_by_persona`` against
  ``client.list_agents(project_id)``, preferring ``{project_key}-{persona}``
  over the trailing-suffix fallback.
- ``project_key`` comes from ``client.get_me().project_key`` (no FS read)
  so the resolver works in remote / isolated projects.
- Other short names (e.g. ``custom``) fall through to literal-name lookup
  and miss if no agent matches.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import typer
from map_types.enums import AgentRole
from map_types.schemas.agent import AgentRead

from cli.runner import _resolve_agent_ref


def _agent(name: str, project_id: uuid.UUID, *, role: AgentRole = AgentRole.agent) -> AgentRead:
    return AgentRead(
        id=uuid.uuid4(),
        name=name,
        role=role,
        project_id=project_id,
        created_at=datetime.now(timezone.utc),
    )


def _client(agents, *, project_key: str | None = "proj-key"):
    client = MagicMock()
    client.list_agents.return_value = agents
    client.get_me.return_value = SimpleNamespace(project_key=project_key)
    return client


def test_persona_short_name_prefers_project_key_prefix() -> None:
    """`participant` resolves to ``{project_key}-participant`` when present,
    not the trailing-suffix fallback."""
    pid = uuid.uuid4()
    preferred = _agent("proj-key-participant", pid)
    suffix_fallback = _agent("multi-agent-platform-participant", pid)
    other = _agent("proj-key-host", pid)
    client = _client([suffix_fallback, preferred, other])

    resolved = _resolve_agent_ref(
        client, pid, "participant", flag="--executor", label="executor"
    )
    assert resolved == preferred.id
    # Project-key prefix path must call get_me for the project_key.
    client.get_me.assert_called()


def test_persona_short_name_falls_back_to_suffix() -> None:
    """When ``{project_key}-{persona}`` is absent, the trailing-suffix
    fallback matches the canonical ``multi-agent-platform-{persona}`` form."""
    pid = uuid.uuid4()
    suffix_match = _agent("multi-agent-platform-host", pid)
    client = _client([suffix_match])

    resolved = _resolve_agent_ref(
        client, pid, "host", flag="--creator", label="creator"
    )
    assert resolved == suffix_match.id


def test_persona_short_name_unknown_exits() -> None:
    """A persona short name with no matching agent triggers the
    literal-name miss path and exits with code 1."""
    pid = uuid.uuid4()
    client = _client([_agent("proj-key-host", pid)])
    with pytest.raises(typer.Exit) as exc:
        _resolve_agent_ref(
            client, pid, "reviewer", flag="--executor", label="executor"
        )
    assert exc.value.exit_code == 1


def test_persona_short_name_works_when_get_me_fails() -> None:
    """``get_me`` is best-effort (e.g. it may 401 in some test harnesses);
    the resolver still picks the suffix fallback when ``project_key`` is
    unavailable."""
    pid = uuid.uuid4()
    fallback = _agent("multi-agent-platform-participant", pid)
    client = MagicMock()
    client.list_agents.return_value = [fallback]
    client.get_me.side_effect = RuntimeError("auth")

    resolved = _resolve_agent_ref(
        client, pid, "participant", flag="--executor", label="executor"
    )
    assert resolved == fallback.id


def test_full_agent_name_path_unchanged() -> None:
    """Passing a full agent_name (e.g. ``proj-key-participant``) must still
    resolve via the exact-name branch and not via the persona short-name
    branch (the suffix happens to look like a persona). This is the
    regression test for the start-writeback path that always derives the
    persona label from the resolved executor's agent name."""
    pid = uuid.uuid4()
    full_name = "proj-key-participant"
    full = _agent(full_name, pid)
    client = _client([full])

    resolved = _resolve_agent_ref(
        client, pid, full_name, flag="--executor", label="executor"
    )
    assert resolved == full.id
    # Must NOT consult persona resolver for full agent_name (would be
    # redundant and would obscure ambiguous-name error messages).
    client.get_me.assert_not_called()


def test_uuid_path_skips_everything() -> None:
    """UUID pass-through stays the fast path; no list_agents / get_me."""
    client = _client([])
    target = uuid.uuid4()
    assert (
        _resolve_agent_ref(client, uuid.uuid4(), str(target), flag="--executor", label="executor")
        == target
    )
    client.list_agents.assert_not_called()
    client.get_me.assert_not_called()
