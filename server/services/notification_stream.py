"""In-process pub/sub for agent notification SSE streams."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from queue import Empty, Queue
from typing import Any

from fastapi.responses import StreamingResponse

HEARTBEAT_SECONDS = 25

_subscribers: dict[uuid.UUID, list[Queue[str]]] = {}
_lock = threading.Lock()


def subscribe(agent_id: uuid.UUID) -> Queue[str]:
    queue: Queue[str] = Queue(maxsize=100)
    with _lock:
        _subscribers.setdefault(agent_id, []).append(queue)
    return queue


def unsubscribe(agent_id: uuid.UUID, queue: Queue[str]) -> None:
    with _lock:
        queues = _subscribers.get(agent_id)
        if not queues:
            return
        try:
            queues.remove(queue)
        except ValueError:
            return
        if not queues:
            _subscribers.pop(agent_id, None)


def publish(agent_id: uuid.UUID, event: dict[str, Any]) -> None:
    payload = json.dumps(event, ensure_ascii=False)
    with _lock:
        queues = list(_subscribers.get(agent_id, []))
    for queue in queues:
        try:
            queue.put_nowait(payload)
        except Exception:
            pass


def publish_many(agent_ids: list[uuid.UUID], event: dict[str, Any]) -> None:
    for agent_id in agent_ids:
        publish(agent_id, event)


async def _sse_generator(agent_id: uuid.UUID):
    queue = subscribe(agent_id)
    try:
        while True:
            try:
                data = await asyncio.to_thread(queue.get, True, HEARTBEAT_SECONDS)
                yield f"data: {data}\n\n"
            except Empty:
                yield ": heartbeat\n\n"
    finally:
        unsubscribe(agent_id, queue)


def notification_sse_response(agent_id: uuid.UUID) -> StreamingResponse:
    return StreamingResponse(
        _sse_generator(agent_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
