import json
import threading
import time
import uuid
from asyncio import QueueEmpty

import pytest

from server.services import notification_stream
from tests._frontmatter import make_valid_plan


def _drain(queue) -> None:
    """排空队列（asyncio.Queue 无同步 get(timeout)，用 get_nowait 轮询）。"""
    while True:
        try:
            queue.get_nowait()
        except QueueEmpty:
            break


def _poll_get(queue, timeout: float) -> str:
    """同步轮询取一帧；超时抛 QueueEmpty（对齐旧 queue.get(timeout) 语义）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return queue.get_nowait()
        except QueueEmpty:
            time.sleep(0.02)
    raise QueueEmpty


def _parse_sse_frame(frame: str) -> tuple[int | None, dict]:
    """解析 ``id: N\\ndata: {...}\\n\\n`` 形式的 SSE frame，返回 (id, data)。"""
    event_id: int | None = None
    data_line = ""
    for line in frame.splitlines():
        if line.startswith("id: "):
            event_id = int(line[4:])
        elif line.startswith("data: "):
            data_line = line[6:]
    return event_id, json.loads(data_line)


def test_notification_stream_publish_subscribe():
    agent_id = uuid.uuid4()
    queue = notification_stream.subscribe(agent_id)
    try:
        notification_stream.publish(
            agent_id,
            {"type": "notification.created", "event": "test.event", "notification_id": "n1"},
        )
        payload = _poll_get(queue, timeout=1)
        event_id, data = _parse_sse_frame(payload)
        assert event_id == 1  # 第一个事件 id 从 1 开始
        assert data["type"] == "notification.created"
        assert data["event"] == "test.event"
    finally:
        notification_stream.unsubscribe(agent_id, queue)


def test_publish_bridges_from_sync_thread_to_subscriber_loop():
    """T12：无 loop 订阅（测试线程）→ 直接 put；真实生产路径为
    SSE generator 事件循环内订阅 + 请求线程 publish——本测试还原后者：
    订阅在事件循环内、publish 来自普通线程，必须经 call_soon_threadsafe
    桥接后仍能被 await 到。"""
    agent_id = uuid.uuid4()
    received: list[str] = []
    publish_error: list[BaseException] = []

    async def scenario():
        import asyncio

        queue = notification_stream.subscribe(agent_id)
        # 从非 loop 线程模拟同步请求线程的 publish。
        def _publish_from_thread():
            try:
                notification_stream.publish(
                    agent_id,
                    {"type": "notification.created", "event": "thread.pub", "notification_id": "n1"},
                )
            except BaseException as exc:  # pragma: no cover - 防御记录
                publish_error.append(exc)

        thread = threading.Thread(target=_publish_from_thread)
        thread.start()
        frame = await asyncio.wait_for(queue.get(), timeout=2)
        received.append(frame)
        thread.join()
        notification_stream.unsubscribe(agent_id, queue)

    import asyncio

    asyncio.run(scenario())
    assert publish_error == []
    assert received, "publish from sync thread must reach the subscriber loop"
    event_id, data = _parse_sse_frame(received[0])
    assert event_id == 1
    assert data["event"] == "thread.pub"


def test_sse_replay_after_reconnect():
    """P2 #9: 断线重连时通过 Last-Event-ID replay 历史事件。

    模拟流程：
    1. agent 收到事件 1, 2, 3（但只有 1, 2 被客户端确认；3 在传输中丢失）
    2. 客户端重连，发送 Last-Event-ID: 2
    3. 服务端 replay id > 2 的事件（即 3）
    """
    agent_id = uuid.uuid4()
    # 发布 3 个事件（无 subscriber，只入 ring buffer）
    for i in range(3):
        notification_stream.publish(
            agent_id,
            {"type": "notification.created", "event": f"e{i + 1}", "notification_id": f"n{i + 1}"},
        )
    # 客户端已收到 id=2，断线重连时 Last-Event-ID: 2 → 应 replay id=3
    replayed = notification_stream._drain_replay(agent_id, last_event_id=2)
    assert len(replayed) == 1
    eid, data = _parse_sse_frame(replayed[0])
    assert eid == 3
    assert data["event"] == "e3"


def test_sse_replay_skips_already_acked():
    """Last-Event-ID: 3 时不应 replay 任何事件（3 已是最新）。"""
    agent_id = uuid.uuid4()
    for i in range(3):
        notification_stream.publish(
            agent_id,
            {"type": "notification.created", "event": f"e{i + 1}", "notification_id": f"n{i + 1}"},
        )
    assert notification_stream._drain_replay(agent_id, last_event_id=3) == []
    # 非法 Last-Event-ID 当作 0 处理：replay 全部
    assert len(notification_stream._drain_replay(agent_id, last_event_id=0)) == 3


def test_sse_parse_last_event_id_handles_invalid():
    assert notification_stream._parse_last_event_id(None) == 0
    assert notification_stream._parse_last_event_id("") == 0
    assert notification_stream._parse_last_event_id("abc") == 0
    assert notification_stream._parse_last_event_id("-5") == 0  # 负数 clamp 到 0
    assert notification_stream._parse_last_event_id("42") == 42


def test_digest_enqueue_does_not_publish_sse_frame(
    client, auth_headers, reviewer, project
):
    """v0.9 (Round 1 §4 + §7.2): the SSE stream MUST NOT carry digest
    notifications — the waker would receive them, only to drop them at the
    ``category == wakeable`` filter. Suppressing digest at the publish gate
    saves bandwidth + keeps session logs clean of phantom wake attempts.
    """
    reviewer_id = uuid.UUID(reviewer["id"])
    queue = notification_stream.subscribe(reviewer_id)
    try:
        exp = client.post(
            f"/api/v1/projects/{project['id']}/experiments",
            headers=auth_headers,
            json={"title": "Digest SSE suppression", "plan": {"content_md": make_valid_plan(body="# p")}},
        ).json()
        # Drain any frame from experiment creation.
        _drain(queue)
        # experiment.phase_changed is a digest event by default.
        client.post(
            f"/api/v1/experiments/{exp['id']}/submit-review", headers=auth_headers
        )

        # No SSE frame should arrive for a digest event.
        with pytest.raises(QueueEmpty):
            _poll_get(queue, timeout=0.5)
    finally:
        notification_stream.unsubscribe(reviewer_id, queue)


def test_wakeable_enqueue_publishes_sse_frame_with_v2_fingerprint(
    client, auth_headers, reviewer, project, db_session
):
    """v0.9 Round 1 §4: wakeable SSE frames must carry the v2 fingerprint
    marker so the waker's resume gate accepts them. Pinned here because
    the SSE frame contract is what ``discover_wake_events`` consumes.

    Driven via the service layer (no HTTP) so we can target a single
    recipient deterministically — emit_kind fans out by persona name, and
    HTTP-driven lifecycle events target only host/participant.
    """
    from map_types.enums import NotificationCategory

    from server.services import notification_service

    reviewer_id = uuid.UUID(reviewer["id"])
    queue = notification_stream.subscribe(reviewer_id)
    try:
        notification_service.enqueue_for_agents(
            db_session,
            recipient_agent_ids=[reviewer_id],
            project_id=uuid.UUID(project["id"]),
            actor_id=uuid.uuid4(),  # different from recipient so exclude_actor=True skips nothing
            event="experiment.lifecycle.withdrawn",
            summary="withdrawn for sse test",
            target_type="experiment",
            target_id=uuid.uuid4(),
            payload=None,
            wakeable=True,
            exclude_actor=False,
        )

        payload = _poll_get(queue, timeout=2)
        _event_id, data = _parse_sse_frame(payload)
        assert data["type"] == "notification.created"
        assert data["event"] == "experiment.lifecycle.withdrawn"
        assert data["category"] == NotificationCategory.wakeable.value
        assert data["fingerprint_version"] == "v2"
    finally:
        notification_stream.unsubscribe(reviewer_id, queue)
