"""Topic comment kind classification (user vs system/informational)."""

from __future__ import annotations

from map_types.enums import TopicCommentKind

from server.services import topic_ack_service


def resolve_topic_comment_kind(body: str) -> TopicCommentKind:
    """Classify comment body; ack markers map to ``system`` for unread_change filtering."""
    if topic_ack_service._ack_kind(body) is not None:
        return TopicCommentKind.system
    return TopicCommentKind.user
