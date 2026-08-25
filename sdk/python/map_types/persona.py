"""Canonical persona identity: trailing ``-<persona>`` on the agent name.

Bootstrap names agents ``{project_key}-{persona}``. Docs and fixtures often
use the package-shaped ``multi-agent-platform-{persona}``. Matching must use
the suffix (or an explicit ``{project_key}-{persona}`` preference), never a
single hard-coded long name.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

CANONICAL_PERSONAS: frozenset[str] = frozenset({"host", "participant", "reviewer"})


def persona_from_agent_name(name: str | None) -> str | None:
    """Return ``host`` / ``participant`` / ``reviewer`` from a trailing suffix.

    ``acme-host``, ``multi-agent-platform-host``, and
    ``multi-agents-platform-host`` all resolve to ``host``. Custom names
    without a canonical suffix return ``None``.
    """
    if not name or "-" not in name:
        return None
    suffix = name.rsplit("-", 1)[1]
    return suffix if suffix in CANONICAL_PERSONAS else None


def preferred_persona_agent_name(project_key: str, persona: str) -> str:
    return f"{project_key}-{persona}"


def pick_agent_by_persona(
    agents: Iterable[Any],
    persona: str,
    *,
    project_key: str | None = None,
) -> Any | None:
    """Pick the agent for ``persona`` within a project's agent list.

    Prefers ``{project_key}-{persona}`` when ``project_key`` is known, then
    falls back to any agent whose name suffix is that persona. Callers pass
    objects with a ``name`` attribute (ORM ``Agent``, SDK ``AgentRead``, or
    a simple namespace in tests).
    """
    if persona not in CANONICAL_PERSONAS:
        return None
    named = [(agent, getattr(agent, "name", None) or "") for agent in agents]
    if project_key:
        preferred = preferred_persona_agent_name(project_key, persona)
        for agent, name in named:
            if name == preferred:
                return agent
    for agent, name in named:
        if persona_from_agent_name(name) == persona:
            return agent
    return None
