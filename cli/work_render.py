"""T33: clear_action → CLI hint rendering (extracted from ``cli/main.py``).

The template table and renderer used to live inline in ``cli.main``; they
are deterministic pure helpers with no runtime CLI state, so they move
here next to the other render helpers (``cli.waker_heartbeat_render``).
``tests/test_clear_action_template.py`` pins the exact rendered strings.
"""

from __future__ import annotations

from typing import Any

_CLEAR_ACTION_TEMPLATES: dict[str, str] = {
    # v0.13 M58: topic write paths are FS-only. The DB-era hints
    # ("--reply-to" thread reply / "advance-round --ack accept") pointed at
    # retired commands; both obligations are now cleared by writing the
    # agent's own round speech file via ``map topic comment``.
    "comment": "map topic comment --topic {topic_id} --file <your-round-speech.md>",
    "ack": "map topic comment --topic {topic_id} --file <your-round-speech.md>  # speech file = your ack (v0.13 M58)",
    "dismiss": "map mention dismiss --id {mention_id}",
    "read": "Read latest comments on topic '{topic_title}'",
}


def render_clear_action_template(work_item: dict[str, Any]) -> str:
    """Render a deterministic CLI hint for a topic work item's clear_action.

    Work items carrying a server-generated ``suggested_command`` (FS plane
    fills the exact command with real slug/args) use it verbatim; the
    static ``clear_action`` templates below are the fallback for payloads
    without one (DB-era items, older servers). The four ``clear_action``
    values map to the matching ``map`` CLI command; ``read`` is a
    free-form reading instruction (no CLI verb).

    The output is purely mechanical — no LLM, no environment lookups —
    so callers can diff the rendered strings in tests.
    """
    suggested = work_item.get("suggested_command")
    if suggested:
        return str(suggested)
    clear_action = work_item.get("clear_action")
    template = _CLEAR_ACTION_TEMPLATES.get(clear_action or "")
    if template is None:
        return f"(unknown clear_action: {clear_action})"
    if clear_action == "read":
        topic_title = work_item.get("topic_title") or "(untitled)"
        return template.format(topic_title=topic_title)
    if clear_action == "dismiss":
        mention_id = (
            work_item.get("mention_id")
            or work_item.get("idempotency_key")
            or work_item.get("source_comment_id")
            or ""
        )
        return template.format(mention_id=mention_id)
    topic_id = work_item.get("topic_id") or ""
    source_comment_id = work_item.get("source_comment_id") or ""
    return template.format(topic_id=topic_id, source_comment_id=source_comment_id)
