"""End-to-end: direct-mode experiment delegated to participant.

plan-mode-direct-execution-productization I2: full happy-path that pins
the direct-mode contract end-to-end:

- host creates ``mode=direct`` experiment in ``draft``.
- host calls ``start --executor <participant>`` → ``running``.
- executor (participant) appears in their own ``executor_assignments``.
- executor calls ``complete`` → experiment goes directly to ``done``
  (NOT ``result_review``).
- reviewer sees no ``pending_result_reviews`` (direct mode skips
  reviewer's approval step entirely).
- host (creator) does NOT see it in their own ``executor_assignments``
  (self-execute carve-out), but DOES see it in ``my_open_experiments``
  while the experiment is still running.

host-review 复核：
- complete API 的 SSE/audit 摘要按终态 phase 区分文案——direct 完成即 done
  → "已完成"；standard 完成进 result_review → "待审批"。
- direct 完成同事务触发 ``topic.close_pending`` 唤醒 host 收尾话题。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Notification
from server.services import todo_service
from tests._frontmatter import make_valid_plan


def _complete_payload() -> dict:
    return {
        "summary": "direct mode done",
        "content_md": "## 结果\nparticipant 自行 complete",
        "metadata": {"metric": 0.01, "pytest_summary": "unit passed"},
    }


def _direct_experiment(
    client, db_session, project, auth_headers, admin_headers, participant: dict,
    *, topic_id: str | None = None,
):
    """Create a direct-mode experiment and start it delegated to participant."""
    project_id = uuid.UUID(project["id"])
    create_payload = {
        "title": "direct-end-to-end",
        "plan": {"content_md": make_valid_plan(body="## 直接执行")},
        "mode": "direct",
    }
    if topic_id is not None:
        create_payload["topic_id"] = topic_id
    create_resp = client.post(
        f"/api/v1/projects/{project_id}/experiments",
        headers=auth_headers,
        json=create_payload,
    )
    assert create_resp.status_code == 201, create_resp.text
    exp_id = create_resp.json()["id"]
    assert create_resp.json()["mode"] == "direct"

    # Direct mode: draft → running, delegated to participant.
    start_resp = client.post(
        f"/api/v1/experiments/{exp_id}/start",
        headers=auth_headers,
        json={"executor_agent_id": participant["id"]},
    )
    assert start_resp.status_code == 200, start_resp.text
    body = start_resp.json()
    assert body["phase"] == "running"
    assert body["executor_agent_id"] == participant["id"]
    return exp_id


def test_direct_executor_complete_goes_to_done_not_result_review(
    client, db_session, project, auth_headers, reviewer, admin_headers
):
    """The participant executor's ``complete`` directly transitions
    ``running → done``; reviewer never sees the result in their todos."""
    participant_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": "direct-executor-participant",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert participant_resp.status_code == 201
    participant = {
        "id": participant_resp.json()["id"],
        "headers": {"Authorization": f"Bearer {participant_resp.json()['api_token']}"},
    }

    exp_id = _direct_experiment(
        client, db_session, project, auth_headers, admin_headers, participant
    )

    # Executor appears in their own executor_assignments.
    executor_todos_before = client.get(
        "/api/v1/agents/me/todos", headers=participant["headers"]
    ).json()
    assert any(
        e["id"] == exp_id for e in executor_todos_before["executor_assignments"]
    )
    # Executor is NOT the creator → no my_open_experiments entry.
    assert all(
        e["id"] != exp_id for e in executor_todos_before["my_open_experiments"]
    )

    # Executor calls complete.
    completed = client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=participant["headers"],
        json=_complete_payload(),
    )
    assert completed.status_code == 200, completed.text
    body = completed.json()
    # Direct mode: complete → done (skips result_review).
    assert body["phase"] == "done"

    # Reviewer has no pending_result_reviews entry for this experiment.
    reviewer_todos = client.get(
        "/api/v1/agents/me/todos", headers=reviewer["headers"]
    ).json()
    assert all(e["id"] != exp_id for e in reviewer_todos.get("pending_result_reviews", []))

    # Executor's executor_assignments drops the experiment (phase != running).
    executor_todos_after = client.get(
        "/api/v1/agents/me/todos", headers=participant["headers"]
    ).json()
    assert all(
        e["id"] != exp_id for e in executor_todos_after["executor_assignments"]
    )


def test_host_creator_carve_out_for_direct_executor(
    client, db_session, project, auth_headers, admin_headers
):
    """The host (creator, NOT executor) sees a delegated direct experiment
    in ``my_open_experiments`` only — never in ``executor_assignments``
    (carve-out keeps self-execute/host delegation partitioning sane)."""
    participant_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": "direct-carveout-participant",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    participant = {
        "id": participant_resp.json()["id"],
        "headers": {"Authorization": f"Bearer {participant_resp.json()['api_token']}"},
    }
    exp_id = _direct_experiment(
        client, db_session, project, auth_headers, admin_headers, participant
    )

    host_todos = client.get(
        "/api/v1/agents/me/todos", headers=auth_headers
    ).json()
    # Host sees the delegated experiment in my_open_experiments.
    assert any(e["id"] == exp_id for e in host_todos["my_open_experiments"])
    # Host is NOT the executor → executor_assignments is empty for this experiment.
    assert all(e["id"] != exp_id for e in host_todos["executor_assignments"])


def test_executor_permission_carve_out_via_service(
    db_session: Session, project: dict, admin_headers: dict, auth_headers: dict, client
):
    """Service-layer: ``executor_assignments`` query excludes rows where
    ``creator_agent_id == agent.id`` (carve-out). Construct two agents
    and verify both partitions stay disjoint."""
    from server.domain.models import Agent, AgentRole, Experiment, ExperimentPhase

    project_id = uuid.UUID(project["id"])
    host = Agent(
        id=uuid.uuid4(),
        project_id=project_id,
        name="host-e2e-direct",
        api_token_hash="x",
        api_token_prefix="x",
        role=AgentRole.agent,
    )
    executor = Agent(
        id=uuid.uuid4(),
        project_id=project_id,
        name="participant-e2e-direct",
        api_token_hash="x",
        api_token_prefix="x",
        role=AgentRole.agent,
    )
    db_session.add_all([host, executor])
    db_session.flush()

    exp = Experiment(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=host.id,
        executor_agent_id=executor.id,
        title="e2e-direct-service",
        phase=ExperimentPhase.running,
        mode="direct",
        current_plan_version=1,
    )
    db_session.add(exp)
    db_session.flush()

    executor_todos = todo_service.get_todos(db_session, executor)
    host_todos = todo_service.get_todos(db_session, host)

    # Executor sees it; host does not (in executor_assignments).
    assert any(e.id == exp.id for e in executor_todos.executor_assignments)
    assert all(e.id != exp.id for e in host_todos.executor_assignments)


# ─── host-review 复核（direct complete API 文案 + 事件桥）───────────────


def test_direct_complete_emits_done_summary_not_pending(
    client, db_session, project, auth_headers, admin_headers
):
    """host-review 复核 #4：direct complete API 发的 SSE/audit 摘要必须用
    「已完成」而不是「待审批」——因为直接进入 done phase，没有审批等待。"""
    participant_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": "direct-summary-participant",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert participant_resp.status_code == 201
    participant = {
        "id": participant_resp.json()["id"],
        "headers": {"Authorization": f"Bearer {participant_resp.json()['api_token']}"},
    }

    exp_id = _direct_experiment(
        client, db_session, project, auth_headers, admin_headers, participant
    )

    completed = client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=participant["headers"],
        json=_complete_payload(),
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["phase"] == "done"

    # API 层 phase_changed 通知：直接查 notification 表（最稳的 audit 链）。
    rows = list(
        db_session.scalars(
            select(Notification).where(
                Notification.target_id == uuid.UUID(exp_id),
                Notification.event == "experiment.phase_changed",
            )
        )
    )
    # 取 complete 那一条（按 created_at desc 最后一条）
    assert rows, "complete API must emit phase_changed notification"
    last = max(rows, key=lambda r: r.created_at)
    assert last.summary is not None
    assert "待审批" not in last.summary, (
        f"direct mode complete must NOT use  待审批 wording; got: {last.summary!r}"
    )
    assert ("已完成" in last.summary) or ("done" in last.summary.lower()), (
        f"direct mode complete must indicate completion; got: {last.summary!r}"
    )
    # SSE payload 也带 completion_state=done（waker/UI 不需要再查 mode）
    payload = last.payload_json or {}
    assert payload.get("completion_state") == "done"


def test_standard_complete_still_emits_pending_summary(
    client, db_session, project, auth_headers, admin_headers, reviewer
):
    """host-review 复核 #4 反向：standard 模式真实 POST /complete → result_review，
    断言响应 phase 与 notification summary/completion_state 都是「待审批」。

    走完整状态机：draft → review（reviewer 提交 review） → approved → running
    （start 显式给 executor_agent_id == 自己，让自身也走 executor 路径） → complete。
    """
    project_id = uuid.UUID(project["id"])
    create_resp = client.post(
        f"/api/v1/projects/{project_id}/experiments",
        headers=auth_headers,
        json={
            "title": "standard-end-to-end-summary",
            "plan": {"content_md": make_valid_plan(body="## 标准模式")},
            "mode": "standard",
            "submit_for_review": True,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    exp_id = create_resp.json()["id"]

    me_resp = client.get("/api/v1/agents/me", headers=auth_headers).json()
    actor_id = me_resp["id"]

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["OK"], "unreasonable_items": []},
    )
    assert review.status_code in (200, 201), review.text

    approve = client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    assert approve.status_code == 200, approve.text

    start = client.post(
        f"/api/v1/experiments/{exp_id}/start",
        headers=auth_headers,
        json={"executor_agent_id": actor_id},
    )
    assert start.status_code == 200, start.text

    completed = client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=auth_headers,
        json=_complete_payload(),
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["phase"] == "result_review"

    rows = list(
        db_session.scalars(
            select(Notification).where(
                Notification.target_id == uuid.UUID(exp_id),
                Notification.event == "experiment.phase_changed",
            )
        )
    )
    assert rows, "complete API must emit phase_changed notification"
    # 取最近一条 complete 期 phase_changed（按 created_at desc）
    last = max(rows, key=lambda r: r.created_at)
    assert "待审批" in last.summary, (
        f"standard mode complete must keep 待审批 wording; got: {last.summary!r}"
    )
    payload = last.payload_json or {}
    assert payload.get("completion_state") == "pending_review"


def test_direct_complete_emits_topic_close_pending(
    client, db_session, project, auth_headers, admin_headers
):
    """host-review 复核 #5：direct 完成即 done 必须同事务触发 topic.close_pending
    唤醒 host 收尾话题——不能因为跳过 reviewer 就漏事件桥。"""
    from pathlib import Path

    from map_fs import topic_id_for_slug, write_topic_index

    # 先建一个 FS 话题（与 accept_result 事件桥的测试 setup 对齐）。
    # 用 uuid 唯一 slug 避免多次测试运行残留（即使共享同一 workspace_path）。
    unique_slug = f"direct-close-pending-{uuid.uuid4().hex[:8]}"
    write_topic_index(
        Path(project["workspace_path"]),
        unique_slug,
        title="Direct EV",
        creator="host",
        overwrite=True,
    )
    topic_id = str(topic_id_for_slug(unique_slug))

    # Bind a host persona agent so the topic's creator=host resolves to a
    # recipient (notification_service._resolve_persona_agent_ids 走 Agent.persona
    # 后缀匹配，没 host agent 就 fanout 给空气，close_pending 不会发出）。
    host_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": "multi-agent-platform-host",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert host_resp.status_code == 201
    host_id = host_resp.json()["id"]

    participant_resp = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={
            "name": "multi-agent-platform-direct-close-pending-participant",
            "role": "agent",
            "project_key": project["project_key"],
        },
    )
    assert participant_resp.status_code == 201
    participant = {
        "id": participant_resp.json()["id"],
        "headers": {"Authorization": f"Bearer {participant_resp.json()['api_token']}"},
    }

    exp_id = _direct_experiment(
        client, db_session, project, admin_headers, admin_headers, participant,
        topic_id=topic_id,
    )

    completed = client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=participant["headers"],
        json=_complete_payload(),
    )
    assert completed.status_code == 200, completed.text

    # Direct 完成应当触发 topic.close_pending 通知给 host creator。
    close_rows = list(
        db_session.scalars(
            select(Notification).where(Notification.event == "topic.close_pending")
        )
    )
    # 至少有一条，且 payload 关联到本实验
    matched = [r for r in close_rows if (r.payload_json or {}).get("experiment_id") == exp_id]
    assert matched, (
        "direct complete must emit topic.close_pending (no event bridge = topic "
        "stuck in ready waiting for stale nudge)"
    )
    # recipient = host creator
    assert any(r.recipient_agent_id == uuid.UUID(host_id) for r in matched)
    # host-review round2 #5：close_pending 文案用「实验已完成」（direct/done
    # 与 standard/accept 同字符串，不再说"已验收"误导走 standard 审批路径）。
    assert any("实验已完成" in r.summary for r in matched), (
        f"close_pending summary must use 实验已完成; "
        f"got summaries={[r.summary for r in matched]!r}"
    )
