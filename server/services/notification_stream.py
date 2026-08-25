"""In-process pub/sub for agent notification SSE streams.

支持 ``Last-Event-ID`` 重连（P2 #9）：

- 每个 agent 维护一个单调递增的 ``event_id``（从 1 开始），每个发布的
  event 都带一个 id。
- SSE frame 形如 ``id: {event_id}\\ndata: {json}\\n\\n``，浏览器
  ``EventSource`` 会自动记录最后接收的 id，断线重连时通过
  ``Last-Event-ID`` 请求头传回。
- 服务端读取该 header，从 per-agent ring buffer 中 replay id 大于它的
  事件，再进入正常的 pub/sub 循环。

T12（2026-08）：传输从 ``threading.Queue`` + ``asyncio.to_thread`` 阻塞
等待改为 ``asyncio.Queue``。原实现每个 SSE 连接长期占住一个 anyio 线程
池线程（默认仅 40 个，连接数上限被线程池钉死，心跳期间线程空转）；现在
连接只在事件循环上挂起 ``wait_for``，线程归零。``publish`` 从同步请求
线程发起，经 ``loop.call_soon_threadsafe`` 桥接回订阅者的事件循环；
``loop=None`` 的订阅者（无事件循环的测试线程）退化为直接 put，仅用于
单线程测试路径。

注意：本实现是 in-process（重启即丢），ring buffer 容量 200 条，覆盖
常见的网络抖动断线重连场景。跨进程或重启场景需要持久化事件历史，
那是后续 P3 的范围。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from fastapi.responses import StreamingResponse

HEARTBEAT_SECONDS = 25
# 每个 agent 保留最近多少条事件用于 Last-Event-ID replay。
# 200 条对 wakeable 通知足够（远多于一次 remind 周期内的量），
# 内存开销可控（每条 ~1KB JSON × 200 × N agents）。
REPLAY_BUFFER_SIZE = 200


@dataclass
class _Subscriber:
    """一个 SSE 连接的订阅端点。

    ``queue`` 属于 ``loop``（SSE generator 所在的事件循环）；``loop=None``
    表示订阅发生在无事件循环的线程（仅测试），publish 走直接 put。
    """

    queue: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue(maxsize=100))
    loop: asyncio.AbstractEventLoop | None = None


_subscribers: dict[uuid.UUID, list[_Subscriber]] = {}
# per-agent 单调事件 id 计数器 + ring buffer。
# (next_id, [(event_id, payload_json), ...])
_event_seqs: dict[uuid.UUID, int] = {}
_replay_buffers: dict[uuid.UUID, deque[tuple[int, str]]] = {}
_lock = threading.Lock()


def subscribe(agent_id: uuid.UUID) -> asyncio.Queue[str]:
    """注册一个订阅者，返回其事件队列。

    生产路径下在 SSE generator（事件循环内）调用——订阅者记录当前
    loop，publish 经 ``call_soon_threadsafe`` 桥接。无运行 loop 的调用
    （测试线程）记录 ``loop=None``，publish 直接 put。
    """
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    subscriber = _Subscriber(loop=loop)
    with _lock:
        _subscribers.setdefault(agent_id, []).append(subscriber)
    return subscriber.queue


def unsubscribe(agent_id: uuid.UUID, queue: asyncio.Queue[str]) -> None:
    with _lock:
        subscribers = _subscribers.get(agent_id)
        if not subscribers:
            return
        for index, subscriber in enumerate(subscribers):
            if subscriber.queue is queue:
                del subscribers[index]
                break
        else:
            return
        if not subscribers:
            _subscribers.pop(agent_id, None)


def _safe_put(queue: asyncio.Queue[str], frame: str) -> None:
    """在目标 loop 上入队；慢消费者（队列满）丢帧不阻塞发布方。"""
    with contextlib.suppress(Exception):
        queue.put_nowait(frame)


def publish(agent_id: uuid.UUID, event: dict[str, Any]) -> None:
    """发布一个事件，分配单调 id 并写入 per-agent ring buffer。

    event dict 会被原样序列化（caller 看到的字段不变），SSE 层在 frame
    外面套 ``id:`` 行；客户端解析时 ``event.id`` 即此 id。
    """
    payload = json.dumps(event, ensure_ascii=False)
    with _lock:
        _event_seqs[agent_id] = _event_seqs.get(agent_id, 0) + 1
        event_id = _event_seqs[agent_id]
        buf = _replay_buffers.setdefault(agent_id, deque(maxlen=REPLAY_BUFFER_SIZE))
        buf.append((event_id, payload))
        subscribers = list(_subscribers.get(agent_id, []))
    frame = f"id: {event_id}\ndata: {payload}\n\n"
    for subscriber in subscribers:
        if subscriber.loop is None:
            # 无 loop 订阅者（测试线程）：同线程直接 put。
            _safe_put(subscriber.queue, frame)
            continue
        # 从同步请求线程把入队调度回 SSE generator 的事件循环。
        with contextlib.suppress(RuntimeError):
            subscriber.loop.call_soon_threadsafe(_safe_put, subscriber.queue, frame)


def publish_many(agent_ids: list[uuid.UUID], event: dict[str, Any]) -> None:
    for agent_id in agent_ids:
        publish(agent_id, event)


def _drain_replay(agent_id: uuid.UUID, last_event_id: int) -> list[str]:
    """返回 agent 的 ring buffer 中 id > last_event_id 的事件 frames。"""
    with _lock:
        buf = _replay_buffers.get(agent_id)
        if not buf:
            return []
        return [f"id: {eid}\ndata: {payload}\n\n" for eid, payload in buf if eid > last_event_id]


def _parse_last_event_id(header_value: str | None) -> int:
    """解析 ``Last-Event-ID`` header，非法或缺失返回 0。"""
    if not header_value:
        return 0
    try:
        return max(0, int(header_value.strip()))
    except (ValueError, TypeError):
        return 0


async def _sse_generator(agent_id: uuid.UUID, last_event_id: int = 0):
    # 1. 先 replay 历史事件（id > last_event_id）
    for frame in _drain_replay(agent_id, last_event_id):
        yield frame
    # 2. 再进入正常 pub/sub 循环——T12：在事件循环上挂起等待而非占住
    # 线程池线程，超时降级为 SSE 心跳注释行。
    queue = subscribe(agent_id)
    try:
        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                yield data
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
    finally:
        unsubscribe(agent_id, queue)


def notification_sse_response(agent_id: uuid.UUID, last_event_id_header: str | None = None) -> StreamingResponse:
    last_event_id = _parse_last_event_id(last_event_id_header)
    return StreamingResponse(
        _sse_generator(agent_id, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            # 告知客户端支持 Last-Event-ID 重连（虽然 EventSource 默认就支持，
            # 显式声明便于其他客户端实现识别）。
            "X-Accel-Allow-Last-Event-ID": "true",
        },
    )
