"""fs-persona-writeback-followup: ``resolve_writer_persona`` regression.

Root cause: ``MAPClient.get_me()`` returns ``AgentRead`` (no ``persona``
field). The old ``getattr(me, "persona", None) or "host"`` therefore always
defaulted to ``"host"`` for any executor / creator write-back — including
delegated ``participant`` executors and ``reviewer`` creators — silently
mis-attributing the FS ``index.md`` writer.

Helper resolution order (strict):
  1. ``me.persona`` when set (forward-compat).
  2. ``persona_from_agent_name(me.name)`` — canonical ``-{persona}`` suffix.
  3. Conservative ``"host"`` fallback (anonymous / custom names).

The fallback is intentional and biased toward ``host`` so an unrecognized
writer doesn't crash a phase transition; correctness comes from the strict
order, not from omitting the fallback.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from cli.commands.experiment import resolve_writer_persona


def _agentread(name: str) -> SimpleNamespace:
    """Bare ``AgentRead``-shaped namespace — no ``persona`` attribute.

    Mirrors the SDK ``AgentRead`` schema exactly: ``id``, ``name``, ``role``,
    ``project_id``, ``project_key``, ``created_at``. Crucially no
    ``persona`` field, which is the entire reason this helper exists.
    """
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        role="agent",
        project_id=uuid.uuid4(),
        project_key="acme",
        created_at="2026-09-01T00:00:00",
    )


def test_participant_suffix_without_persona_attribute_returns_participant() -> None:
    """The headline regression: ``-participant`` name, no persona attr → participant."""
    me = _agentread("multi-agent-platform-plan-dogfood-participant")
    # Sanity guard: AgentRead has no ``persona`` attribute. If this fails,
    # AgentRead has gained a ``persona`` field and the helper should still
    # behave — but the test fixture becomes the wrong shape.
    assert not hasattr(me, "persona")
    assert resolve_writer_persona(me) == "participant"


def test_host_suffix_without_persona_attribute_returns_host() -> None:
    """Bootstrap agent names like ``acme-host`` resolve to ``host``."""
    me = _agentread("acme-host")
    assert not hasattr(me, "persona")
    assert resolve_writer_persona(me) == "host"


def test_reviewer_suffix_without_persona_attribute_returns_reviewer() -> None:
    """Reviewer writers must not be collapsed into ``host``."""
    me = _agentread("acme-reviewer")
    assert not hasattr(me, "persona")
    assert resolve_writer_persona(me) == "reviewer"


def test_project_key_prefix_participant_resolves_participant() -> None:
    """``{project_key}-participant`` is the canonical boot shape too."""
    me = _agentread("multi-agent-platform-plan-dogfood-participant")
    assert resolve_writer_persona(me) == "participant"


def test_explicit_persona_attribute_wins_over_name_suffix() -> None:
    """Forward-compat: if a future ``AgentRead`` carries persona, trust it.

    Even when the name suffix says something else (e.g. user renamed the
    agent but the schema-side persona attribute still pins it), the
    explicit attribute wins. This keeps behaviour deterministic when
    AgentRead eventually gains a ``persona`` field.
    """
    me = SimpleNamespace(
        name="acme-host",  # suffix says host
        persona="participant",  # but explicit attribute overrides
    )
    assert resolve_writer_persona(me) == "participant"


def test_anonymous_name_without_suffix_falls_back_to_host() -> None:
    """Anonymous / custom name (no canonical suffix) → conservative host fallback."""
    me = _agentread("custom-bot")
    assert resolve_writer_persona(me) == "host"


def test_none_name_falls_back_to_host() -> None:
    """Defensive: ``me.name == None`` (misbehaving caller) must not crash."""
    me = SimpleNamespace(name=None)
    assert resolve_writer_persona(me) == "host"


def test_does_not_use_dash_prefix_matching() -> None:
    """``acme-hostile`` must NOT resolve to ``host`` — only trailing suffix counts.

    ``persona_from_agent_name`` is the canonical parser; this test pins the
    helper against future regressions that might use ``str.startswith``.
    """
    me = _agentread("acme-hostile")
    assert resolve_writer_persona(me) == "host"  # fallback, not "hostile"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("acme-host", "host"),
        ("acme-participant", "participant"),
        ("acme-reviewer", "reviewer"),
        ("multi-agent-platform-host", "host"),
        ("multi-agents-platform-participant", "participant"),
        ("multi-agent-platform-plan-dogfood-reviewer", "reviewer"),
    ],
)
def test_canonical_suffix_table(name: str, expected: str) -> None:
    """Parametrised table covering the well-known bootstrap shapes."""
    me = _agentread(name)
    assert resolve_writer_persona(me) == expected
