"""CLI helper tests for (d) clear_action templates + (c part 2) deprecation detection.

The two helpers are deterministic pure functions (``render_clear_action_template``
defined in ``cli.main``，``detect_deprecated_aliases`` defined in ``cli.runner``):

* :func:`render_clear_action_template` — renders a literal CLI hint per
  ``clear_action`` value. Output is a plain string with no LLM dependency
  and no environment lookups, so tests assert exact text.
* :func:`detect_deprecated_aliases` — walks a JSON/YAML-decodable payload
  and emits one stderr warning per legacy alias key found. Output is
  deterministic (sorted, deduped) so tests assert exact lines.

The acceptance (c part 2) end-to-end behaviour (warning appearing on
stderr of ``map work`` when the API returns a payload that still carries
``advance_round_pending_since`` or ``partition_visibility``) is exercised
in ``tests/test_work_summary.py::test_cli_work_emits_deprecation_warning_on_legacy_alias``.
"""

from __future__ import annotations

import pytest

from cli.main import (
    _CLEAR_ACTION_TEMPLATES,
    render_clear_action_template,
)
from cli.runner import detect_deprecated_aliases

pytestmark = pytest.mark.slow


def test_render_clear_action_comment_template_is_deterministic():
    """The 'comment' clear_action maps to ``map fs comment`` (v0.13 M58 FS-only)."""
    item = {
        "kind": "pending_topic_reply",
        "clear_action": "comment",
        "topic_id": "11111111-1111-1111-1111-111111111111",
        "source_comment_id": "22222222-2222-2222-2222-222222222222",
        "topic_title": "demo",
    }
    rendered = render_clear_action_template(item)
    expected = (
        "map topic comment --topic 11111111-1111-1111-1111-111111111111 "
        "--file <your-round-speech.md>"
    )
    assert rendered == expected
    # Determinism: rendering twice yields byte-identical output (no timestamps).
    assert render_clear_action_template(item) == rendered


def test_render_clear_action_ack_template_is_deterministic():
    """The 'ack' clear_action maps to the FS speech-file hint (no ack verb since v0.13 M58)."""
    item = {
        "kind": "round_ack",
        "clear_action": "ack",
        "topic_id": "33333333-3333-3333-3333-333333333333",
        "topic_title": "round ack demo",
    }
    rendered = render_clear_action_template(item)
    assert rendered == (
        "map topic comment --topic 33333333-3333-3333-3333-333333333333 "
        "--file <your-round-speech.md>  # speech file = your ack (v0.13 M58)"
    )


def test_render_clear_action_dismiss_uses_mention_id():
    """The 'dismiss' clear_action prefers ``mention_id`` over fallback fields."""
    item = {
        "kind": "mention",
        "clear_action": "dismiss",
        "mention_id": "44444444-4444-4444-4444-444444444444",
        "idempotency_key": "fallback",
        "source_comment_id": "fallback-2",
    }
    rendered = render_clear_action_template(item)
    assert rendered == "map mention dismiss --id 44444444-4444-4444-4444-444444444444"


def test_render_clear_action_dismiss_falls_back_to_idempotency_key():
    """When ``mention_id`` is absent, ``idempotency_key`` is used as a stable handle."""
    item = {
        "kind": "mention",
        "clear_action": "dismiss",
        "idempotency_key": "stale-mention-key",
    }
    rendered = render_clear_action_template(item)
    assert rendered == "map mention dismiss --id stale-mention-key"


def test_render_clear_action_read_is_a_reading_hint_not_a_cli_verb():
    """The 'read' clear_action is informational — no ``map`` CLI verb is implied."""
    item = {
        "kind": "unread_change",
        "clear_action": "read",
        "topic_title": "status updates",
    }
    rendered = render_clear_action_template(item)
    assert rendered == "Read latest comments on topic 'status updates'"
    # Sanity: it does NOT start with 'map ' — keeps the distinction visible to humans.
    assert not rendered.startswith("map ")


def test_render_clear_action_unknown_returns_placeholder():
    """Unknown clear_action values render as a literal placeholder, not a CLI hint."""
    item = {"clear_action": "rotate-left"}
    rendered = render_clear_action_template(item)
    assert rendered == "(unknown clear_action: rotate-left)"


def test_render_clear_action_no_llm_dependency():
    """The templates must be a static dict lookup — no I/O, no random, no time.

    This guards against an accidental LLM call or env read being smuggled into
    the helper. We assert that:

    * every clear_action value maps to a string template,
    * the templates do not include placeholder tokens beyond {topic_id},
      {topic_title}, {source_comment_id}, {mention_id}.
    """
    allowed = {"{topic_id}", "{topic_title}", "{source_comment_id}", "{mention_id}"}
    for action, template in _CLEAR_ACTION_TEMPLATES.items():
        assert isinstance(template, str)
        # Every placeholder in the template must come from the allowed set.
        import string

        formatter = string.Formatter()
        for _literal_text, field_name, _format_spec, _conversion in formatter.parse(template):
            if field_name is None:
                continue
            assert f"{{{field_name}}}" in allowed, (
                f"clear_action {action!r} template uses unknown placeholder {field_name!r}"
            )


def test_detect_deprecated_aliases_warns_on_legacy_keys():
    """Each legacy alias key in the payload yields one deterministic warning."""
    payload = {
        "todos": {
            "pending_round_acks": [
                {"advance_round_pending_since": "2026-07-01T00:00:00Z", "topic_id": "x"},
            ],
            "buckets": [
                {"kind": "mention", "partition_visibility": "all"},
            ],
        }
    }
    warnings = detect_deprecated_aliases(payload)
    assert warnings == [
        "Warning: 'advance_round_pending_since' is deprecated; use 'stale_since' instead.",
        "Warning: 'partition_visibility' is deprecated; use 'visibility' instead.",
    ]


def test_detect_deprecated_aliases_is_silent_for_new_keys():
    """No legacy key → no warning."""
    payload = {
        "todos": {
            "pending_round_acks": [
                {"stale_since": "2026-07-01T00:00:00Z", "topic_id": "x"},
            ],
            "buckets": [
                {"kind": "mention", "visibility": "all"},
            ],
        }
    }
    assert detect_deprecated_aliases(payload) == []


def test_detect_deprecated_aliases_walks_nested_structures():
    """Walks lists and dicts at any depth; never raises on arbitrary shapes."""
    payload = {
        "items": [
            {"data": [{"advance_round_pending_since": None}]},
            {"other": {"inner": {"partition_visibility": "host_only"}}},
        ]
    }
    warnings = detect_deprecated_aliases(payload)
    # sorted alphabetically for determinism
    assert warnings == [
        "Warning: 'advance_round_pending_since' is deprecated; use 'stale_since' instead.",
        "Warning: 'partition_visibility' is deprecated; use 'visibility' instead.",
    ]


def test_detect_deprecated_aliases_dedupes_repeated_keys():
    """A legacy key appearing 3 times still yields 1 warning line."""
    payload = {
        "a": {"advance_round_pending_since": None},
        "b": [{"advance_round_pending_since": None}],
        "c": {"nested": {"advance_round_pending_since": None}},
    }
    warnings = detect_deprecated_aliases(payload)
    assert warnings == [
        "Warning: 'advance_round_pending_since' is deprecated; use 'stale_since' instead.",
    ]


def test_detect_deprecated_aliases_handles_non_dict_payloads():
    """Top-level lists / strings / scalars yield no warnings without raising."""
    assert detect_deprecated_aliases([1, 2, 3]) == []
    assert detect_deprecated_aliases("hello") == []
    assert detect_deprecated_aliases(42) == []
    assert detect_deprecated_aliases(None) == []


@pytest.mark.parametrize("clear_action", ["comment", "ack", "dismiss", "read"])
def test_render_clear_action_known_actions_have_templates(clear_action):
    """Sanity: every documented clear_action has a registered template."""
    assert clear_action in _CLEAR_ACTION_TEMPLATES
