"""``map todo clear ...`` sub-app — T45 拆分自 topic.py。"""
from __future__ import annotations

import uuid

import typer

from cli import runner  # module ref: test monkeypatch surface (T23)

todo_app = typer.Typer(help="Todo partition clear routing (explicit_only buckets)")


@todo_app.command("clear")
def todo_clear(
    key: str = typer.Option(..., "--key", help="Work-item idempotency_key or partition id"),
) -> None:
    """Route explicit_only todo partitions to the canonical clear CLI (T1 D7)."""

    if key.startswith("notification:"):
        notification_id = uuid.UUID(key.split(":", 1)[1])
        runner._run(lambda c: c.mark_notification_read(notification_id))
        return
    if key.startswith("action_item:"):
        item_id = uuid.UUID(key.split(":", 1)[1])
        runner._run(lambda c: c.complete_action_item(item_id))
        return
    if key.startswith("my_open_topics:") or key.startswith("topic:"):
        topic_id = uuid.UUID(key.rsplit(":", 1)[-1])
        runner._run(lambda c: c.dismiss_topic(topic_id))
        return
    if key.startswith("unread_change:"):
        topic_id = uuid.UUID(key.split(":", 2)[1])
        runner._run(lambda c: c.mark_topic_read(topic_id))
        return
    raise typer.BadParameter(
        f"unsupported todo clear key {key!r}; explicit_only: notification, action_item, "
        "my_open_topics, unread_change"
    )
