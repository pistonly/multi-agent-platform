"""Shared text helpers for service-layer excerpts.

Centralises the 200-char excerpt previously duplicated as
``mention_service._excerpt``, ``topic_service._topic_comment_excerpt`` and
``todo_service._excerpt``. Co-locating it here also breaks the
``todo_service ↔ topic_work_item_service`` import cycle: ``topic_work_item_service``
used to import these helpers from ``todo_service``, forcing ``todo_service`` to
lazily import ``topic_work_item_service``. The helpers now live in this
dependency-free module.
"""

_EXCERPT_LEN = 200


def excerpt(body: str, *, limit: int = _EXCERPT_LEN) -> str:
    text = body.strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
