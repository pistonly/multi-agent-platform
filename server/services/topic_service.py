"""Topic domain service facade.

Business logic lives in:
- ``topic_lifecycle_service`` — CRUD / summaries / rounds
- ``topic_resolve_service`` — resolve + decisions
- ``topic_action_item_ops`` — action-item mutations + reads
- ``topic_comment_service`` — comments (earlier split)
- ``topic_helpers`` — shared ``_get_topic`` / ``_agent_names_by_ids``

This module re-exports the public (and historically private) API so
existing ``from server.services.topic_service import ...`` call sites
and ``topic_service.<name>`` attribute access keep working.
"""
from __future__ import annotations

from server.services import topic_comment_service
from server.services.topic_action_item_ops import (
    _action_item_read,
    _complete_action_item_no_commit,
    _ensure_action_item_accessor,
    _linked_experiment_phases_batch,
    _suggest_linked_experiment,
    _suggest_linked_experiments_batch,
    cancel_action_item,
    complete_action_item,
    deliver_action_item,
    deliver_action_item_no_commit,
    link_action_item,
    list_action_items,
    mark_stale_action_item,
    mark_wake_sent_action_item,
)
from server.services.topic_helpers import _agent_names_by_ids, _get_topic
from server.services.topic_lifecycle_service import (
    advance_topic_round,
    create_topic,
    dismiss_topic,
    get_topic_detail,
    list_topics,
    mark_topic_read,
    record_participant_round_ack,
    rollback_topic_round,
    set_topic_status,
    soft_delete_topic,
    topic_summaries_for_topics,
    topic_summary,
    update_topic,
)
from server.services.topic_resolve_service import (
    _load_decision,
    list_project_decisions,
    resolve_topic,
    topic_decision_read,
)

# Comment CRUD / tree / seq — re-export for backward compatibility.
_build_comment_tree = topic_comment_service._build_comment_tree
_ensure_comment_seq_values = topic_comment_service._ensure_comment_seq_values
_next_topic_comment_seq = topic_comment_service._next_topic_comment_seq
create_topic_comment = topic_comment_service.create_topic_comment
topic_comment_read = topic_comment_service.topic_comment_read
list_topic_comments = topic_comment_service.list_topic_comments

__all__ = [
    "_agent_names_by_ids",
    "_get_topic",
    "_action_item_read",
    "_complete_action_item_no_commit",
    "_ensure_action_item_accessor",
    "_linked_experiment_phases_batch",
    "_load_decision",
    "_suggest_linked_experiment",
    "_suggest_linked_experiments_batch",
    "_build_comment_tree",
    "_ensure_comment_seq_values",
    "_next_topic_comment_seq",
    "advance_topic_round",
    "cancel_action_item",
    "complete_action_item",
    "create_topic",
    "create_topic_comment",
    "deliver_action_item",
    "deliver_action_item_no_commit",
    "dismiss_topic",
    "get_topic_detail",
    "link_action_item",
    "list_action_items",
    "list_project_decisions",
    "list_topic_comments",
    "list_topics",
    "mark_stale_action_item",
    "mark_topic_read",
    "mark_wake_sent_action_item",
    "record_participant_round_ack",
    "resolve_topic",
    "rollback_topic_round",
    "set_topic_status",
    "soft_delete_topic",
    "topic_comment_read",
    "topic_decision_read",
    "topic_summaries_for_topics",
    "topic_summary",
    "update_topic",
]
