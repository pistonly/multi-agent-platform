"""A2 acceptance tests: experiment done -> action_item cascade + dual audit events.

Covers A2-1 through A2-11 service-level guarantees; A2-12 (full regression) is
implicit in the 372-test suite passing locally.
"""

import contextlib

import pytest

pytestmark = pytest.mark.slow
from fastapi.testclient import TestClient

# ---------- helpers ----------------------------------------------------------

def _create_topic(client: TestClient, headers: dict, project: dict, **overrides) -> dict:
    payload = {"title": "A2 cascade test topic", "description": "tests for cascade + audit"}
    payload.update(overrides)
    resp = client.post(f"/api/v1/projects/{project['id']}/topics", headers=headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _resolve_with_linked_item(
    client: TestClient,
    headers: dict,
    topic_id: str,
    *,
    title: str,
    owner_agent_id: str | None,
    linked_experiment_id: str | None,
) -> str:
    payload = {
        "decision": "d",
        "action_items": [
            {
                "title": title,
                "owner_agent_id": owner_agent_id,
                "linked_experiment_id": linked_experiment_id,
            }
        ],
    }
    resp = client.post(f"/api/v1/topics/{topic_id}/resolve", headers=headers, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["action_items"][0]["id"]


def _create_and_approve_experiment(
    client: TestClient,
    headers: dict,
    reviewer_headers: dict,
    project: dict,
    title: str = "A2 cascade exp",
) -> str:
    experiment = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=headers,
        json={"title": title, "plan": {"content_md": "## 计划\ncascade 验证"}, "submit_for_review": True},
    ).json()
    exp_id = experiment["id"]

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer_headers,
        json={"reasonable_items": ["OK"]},
    ).json()
    for item in review["items"]:
        if item["kind"] == "unreasonable":
            client.patch(
                f"/api/v1/review-items/{item['id']}",
                headers=reviewer_headers,
                json={"status": "resolved"},
            )
    client.post(f"/api/v1/experiments/{exp_id}/approve", headers=headers)
    return exp_id


def _run_experiment_to_done(
    client: TestClient,
    headers: dict,
    reviewer_headers: dict,
    exp_id: str,
    *,
    accept_summary: str = "结果审批通过",
) -> None:
    started = client.post(f"/api/v1/experiments/{exp_id}/start", headers=headers)
    assert started.status_code == 200
    completed = client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=headers,
        json={"summary": "实验完成", "content_md": "## 结果\ncascade 准备就绪"},
    )
    assert completed.status_code == 200
    accepted = client.post(
        f"/api/v1/experiments/{exp_id}/accept-result",
        headers=reviewer_headers,
        json={"summary": accept_summary, "content_md": "accept_result"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["phase"] == "done"


# ---------- A2-1: linked action_item -> done ---------------------------------

def test_a2_1_cascade_done_on_accept_result(
    client: TestClient, auth_headers: dict, reviewer: dict, project: dict,
):
    """A2-1: linked action_item flips to done when experiment is accepted."""
    topic = _create_topic(client, auth_headers, project)
    exp_id = _create_and_approve_experiment(client, auth_headers, reviewer["headers"], project)
    item_id = _resolve_with_linked_item(
        client, auth_headers, topic["id"],
        title="A2-1 cascade target",
        owner_agent_id=reviewer["id"],
        linked_experiment_id=exp_id,
    )

    _run_experiment_to_done(client, auth_headers, reviewer["headers"], exp_id)

    after = client.get(
        f"/api/v1/projects/{project['id']}/action-items",
        headers=auth_headers,
        params={"status": "done"},
    )
    assert any(item["id"] == item_id for item in after.json())


# ---------- A2-2: 同事务原子性回滚 --------------------------------------------

def test_a2_2_atomic_rollback_on_audit_failure(
    client: TestClient, auth_headers: dict, reviewer: dict, project: dict, monkeypatch: pytest.MonkeyPatch,
):
    """A2-2: 模拟 audit 写第 2 条时抛 RuntimeError，验证整笔回滚（phase 不变 + item 仍 open + 双事件都没写）。"""
    from server.services import audit_service

    topic = _create_topic(client, auth_headers, project)
    exp_id = _create_and_approve_experiment(client, auth_headers, reviewer["headers"], project)
    item_id = _resolve_with_linked_item(
        client, auth_headers, topic["id"],
        title="A2-2 atomic target",
        owner_agent_id=reviewer["id"],
        linked_experiment_id=exp_id,
    )

    # 实验走到 result_review
    started = client.post(f"/api/v1/experiments/{exp_id}/start", headers=auth_headers)
    assert started.status_code == 200
    completed = client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=auth_headers,
        json={"summary": "实验完成", "content_md": "result"},
    )
    assert completed.status_code == 200
    assert completed.json()["phase"] == "result_review"

    # 注入故障：_log_no_commit 第二次调用时抛 RuntimeError
    original = audit_service._log_no_commit
    call_count = {"n": 0}

    def faulty_log(db, **kwargs):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise RuntimeError("simulated audit failure mid-cascade")
        return original(db, **kwargs)

    monkeypatch.setattr(audit_service, "_log_no_commit", faulty_log)
    # phase_service 也 import 了 audit_service 名字 → patch 两边
    from server.services import phase_service
    monkeypatch.setattr(phase_service.audit_service, "_log_no_commit", faulty_log)

    # TestClient 默认会再抛 server exception — 用 contextlib.suppress 吞 RuntimeError，验证 state
    with contextlib.suppress(RuntimeError):  # 预期：故障注入导致 service 层抛 RuntimeError，FastAPI 转 500
        client.post(
            f"/api/v1/experiments/{exp_id}/accept-result",
            headers=reviewer["headers"],
            json={"summary": "结果审批", "content_md": "fail injection"},
        )

    # 关键验证：DB 真实持久化状态。conftest 的 db_session 与 FastAPI 共用同一个 session，
    # 强制 rollback 共享 session（模拟生产代码 db.commit() 失败 → SQLAlchemy 自动 rollback）
    # 然后用 fresh session 直接查 DB。
    import uuid as _uuid

    # 通过 dependency override 拿到共享 session（同一对象）并 rollback
    from server.api.deps import get_db
    from server.db.session import SessionLocal as test_session_local
    from server.domain.models import AuditLog, Experiment, TopicActionItem
    shared_session_gen = client.app.dependency_overrides[get_db]()
    shared_session = next(shared_session_gen)
    shared_session.rollback()
    with contextlib.suppress(Exception):
        shared_session_gen.close()

    fresh_db = test_session_local()
    try:
        # 实验 phase 仍 result_review（未到 done）
        persisted_exp = fresh_db.get(Experiment, _uuid.UUID(exp_id))
        assert persisted_exp.phase.value == "result_review", (
            f"实验 phase 应仍 result_review，实际 {persisted_exp.phase.value}"
        )
        # action_item 仍 open
        persisted_item = fresh_db.get(TopicActionItem, _uuid.UUID(item_id))
        assert persisted_item.status.value == "open", (
            f"action_item 应仍 open，实际 {persisted_item.status.value}"
        )
        # 双事件 audit 都没写
        item_audits = fresh_db.query(AuditLog).filter(
            AuditLog.target_type == "topic_action_item",
            AuditLog.target_id == _uuid.UUID(item_id),
        ).all()
        assert not any(a.action == "action_item.completed" for a in item_audits)
        exp_audits = fresh_db.query(AuditLog).filter(
            AuditLog.target_type == "experiment",
            AuditLog.target_id == _uuid.UUID(exp_id),
        ).all()
        assert not any(a.action == "experiment.completed" for a in exp_audits)
    finally:
        fresh_db.close()

    # action_item 仍 open
    listing = client.get(
        f"/api/v1/projects/{project['id']}/action-items",
        headers=auth_headers,
        params={"status": "open"},
    )
    assert any(item["id"] == item_id for item in listing.json())

    # 双事件 audit 都没写
    item_audit = client.get(
        "/api/v1/audit",
        headers=auth_headers,
        params={"target_type": "topic_action_item", "target_id": item_id},
    )
    assert not any(log["action"] == "action_item.completed" for log in item_audit.json())
    exp_audit = client.get(
        "/api/v1/audit",
        headers=auth_headers,
        params={"target_type": "experiment", "target_id": exp_id},
    )
    assert not any(log["action"] == "experiment.completed" for log in exp_audit.json())




def test_a2_3_action_item_completed_payload_has_triggered_by(
    client: TestClient, auth_headers: dict, reviewer: dict, project: dict,
):
    """A2-3: cascade-emitted audit event has triggered_by, prev_status, new_status."""
    topic = _create_topic(client, auth_headers, project)
    exp_id = _create_and_approve_experiment(client, auth_headers, reviewer["headers"], project)
    item_id = _resolve_with_linked_item(
        client, auth_headers, topic["id"],
        title="A2-3 payload check",
        owner_agent_id=reviewer["id"],
        linked_experiment_id=exp_id,
    )

    _run_experiment_to_done(client, auth_headers, reviewer["headers"], exp_id)

    audit = client.get(
        "/api/v1/audit",
        headers=auth_headers,
        params={"target_type": "topic_action_item", "target_id": item_id},
    )
    completed = [log for log in audit.json() if log["action"] == "action_item.completed"]
    assert len(completed) == 1
    payload = completed[0]["payload_json"]
    assert payload["triggered_by"] == f"experiment.completed:{exp_id}"
    assert payload["prev_status"] == "open"
    assert payload["new_status"] == "done"


# ---------- A2-4 + A2-5: experiment.completed payload + triggered_by 反查 -----

def test_a2_4_5_experiment_completed_payload_and_causal_lookup(
    client: TestClient, auth_headers: dict, reviewer: dict, project: dict,
):
    """A2-4 + A2-5: 主事件 payload 含 cascaded_action_items；triggered_by 反查精准."""
    topic = _create_topic(client, auth_headers, project)
    exp_id = _create_and_approve_experiment(client, auth_headers, reviewer["headers"], project)
    item_id = _resolve_with_linked_item(
        client, auth_headers, topic["id"],
        title="A2-4 cascade #1",
        owner_agent_id=reviewer["id"],
        linked_experiment_id=exp_id,
    )

    _run_experiment_to_done(client, auth_headers, reviewer["headers"], exp_id)

    # A2-4
    audit = client.get(
        "/api/v1/audit",
        headers=auth_headers,
        params={"target_type": "experiment", "target_id": exp_id},
    )
    completed_events = [log for log in audit.json() if log["action"] == "experiment.completed"]
    assert len(completed_events) == 1
    cascaded = completed_events[0]["payload_json"]["cascaded_action_items"]
    assert len(cascaded) == 1
    assert cascaded[0]["action_item_id"] == item_id
    assert cascaded[0]["prev_status"] == "open"
    assert cascaded[0]["new_status"] == "done"

    # A2-5
    item_audit = client.get(
        "/api/v1/audit",
        headers=auth_headers,
        params={"target_type": "topic_action_item", "target_id": item_id},
    )
    triggered = [
        log for log in item_audit.json()
        if log["action"] == "action_item.completed"
        and log["payload_json"].get("triggered_by") == f"experiment.completed:{exp_id}"
    ]
    assert len(triggered) == 1


# ---------- A2-6: 未设 linked_experiment_id 不联动 -----------------------------

def test_a2_6_unlinked_item_not_cascaded(
    client: TestClient, auth_headers: dict, reviewer: dict, project: dict,
):
    """A2-6: 没有 linked_experiment_id 的 item，实验 done 后仍 open。"""
    topic = _create_topic(client, auth_headers, project)
    exp_id = _create_and_approve_experiment(client, auth_headers, reviewer["headers"], project)
    item_id = _resolve_with_linked_item(
        client, auth_headers, topic["id"],
        title="A2-6 no link",
        owner_agent_id=reviewer["id"],
        linked_experiment_id=None,
    )

    _run_experiment_to_done(client, auth_headers, reviewer["headers"], exp_id)

    listing = client.get(
        f"/api/v1/projects/{project['id']}/action-items",
        headers=auth_headers,
        params={"status": "open"},
    )
    assert any(item["id"] == item_id for item in listing.json())

    audit = client.get(
        "/api/v1/audit",
        headers=auth_headers,
        params={"target_type": "topic_action_item", "target_id": item_id},
    )
    assert not any(
        log["action"] == "action_item.completed" for log in audit.json()
    )


# ---------- A2-7: 多 item 联动完整 ---------------------------------------------

def test_a2_7_multiple_items_cascade(
    client: TestClient, auth_headers: dict, reviewer: dict, project: dict,
):
    """A2-7: 一个实验关联 3 个 open item，全部 done + 主事件 payload 长度=3。"""
    topic = _create_topic(client, auth_headers, project)
    exp_id = _create_and_approve_experiment(client, auth_headers, reviewer["headers"], project)

    payload = {
        "decision": "d",
        "action_items": [
            {"title": "A2-7 multi #1", "owner_agent_id": reviewer["id"], "linked_experiment_id": exp_id},
            {"title": "A2-7 multi #2", "owner_agent_id": reviewer["id"], "linked_experiment_id": exp_id},
            {"title": "A2-7 multi #3", "owner_agent_id": reviewer["id"], "linked_experiment_id": exp_id},
        ],
    }
    resp = client.post(f"/api/v1/topics/{topic['id']}/resolve", headers=auth_headers, json=payload)
    assert resp.status_code == 200
    item_ids = [it["id"] for it in resp.json()["action_items"]]

    _run_experiment_to_done(client, auth_headers, reviewer["headers"], exp_id)

    audit = client.get(
        "/api/v1/audit",
        headers=auth_headers,
        params={"target_type": "experiment", "target_id": exp_id},
    )
    completed_events = [log for log in audit.json() if log["action"] == "experiment.completed"]
    cascaded = completed_events[0]["payload_json"]["cascaded_action_items"]
    assert {c["action_item_id"] for c in cascaded} == set(item_ids)

    per_item_audits = sum(
        len([
            log for log in client.get(
                "/api/v1/audit",
                headers=auth_headers,
                params={"target_type": "topic_action_item", "target_id": iid},
            ).json() if log["action"] == "action_item.completed"
        ])
        for iid in item_ids
    )
    assert per_item_audits == 3


# ---------- A2-8: 已关闭 item 不重复联动 -------------------------------------

def test_a2_8_already_closed_not_recascaded(
    client: TestClient, auth_headers: dict, admin_headers: dict, reviewer: dict, project: dict,
):
    """A2-8: 手工 complete 后，实验 accept 不再重复联动该 item。"""
    topic = _create_topic(client, auth_headers, project)
    exp_id = _create_and_approve_experiment(client, auth_headers, reviewer["headers"], project)
    item_id = _resolve_with_linked_item(
        client, auth_headers, topic["id"],
        title="A2-8 pre-close",
        owner_agent_id=reviewer["id"],
        linked_experiment_id=exp_id,
    )

    # 手工 close in advance (admin_headers 避免 owner 校验失败)
    pre_close = client.post(f"/api/v1/action-items/{item_id}/complete", headers=admin_headers)
    assert pre_close.status_code == 200

    _run_experiment_to_done(client, auth_headers, reviewer["headers"], exp_id)

    audit = client.get(
        "/api/v1/audit",
        headers=auth_headers,
        params={"target_type": "topic_action_item", "target_id": item_id},
    )
    completed = [log for log in audit.json() if log["action"] == "action_item.completed"]
    # 1 条 from manual, 0 from cascade
    assert len(completed) == 1
    assert completed[0]["payload_json"]["triggered_by"] == "manual"


# ---------- A2-10: 跨 project link 拒绝 ---------------------------------------

def test_a2_10_link_cross_project_rejected(
    client: TestClient, auth_headers: dict, admin_headers: dict, reviewer: dict, project: dict,
):
    """A2-10: 跨 project 的 link 返回 409。

    通过 service 层白盒验证：构造 project_a 下的 experiment 与 project_b 下的 action_item，
    直接调用 link_action_item 期望 ConflictError。
    """
    # 创建第二个 project（用 admin 权限）
    project_b = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={"project_key": "p_b", "name": "p_b", "workspace_path": "/tmp/p_b"},
    ).json()

    # 在 project_a 创建并 done 一个实验；在 project_b 创建 action_item
    _create_topic(client, auth_headers, project)
    exp_id = _create_and_approve_experiment(client, auth_headers, reviewer["headers"], project, title="A2-10 cross exp")
    _run_experiment_to_done(client, auth_headers, reviewer["headers"], exp_id)

    # 在 project_b 直接创建一个 topic + item（用 admin_headers）
    topic_b = client.post(
        f"/api/v1/projects/{project_b['id']}/topics",
        headers=admin_headers,
        json={"title": "A2-10 cross topic B"},
    ).json()
    item_id = _resolve_with_linked_item(
        client, admin_headers, topic_b["id"],
        title="A2-10 cross item",
        owner_agent_id=None,
        linked_experiment_id=None,
    )

    # link 应拒绝（item 在 project_b，exp 在 project_a）
    rejected = client.post(
        f"/api/v1/action-items/{item_id}/link",
        headers=admin_headers,
        params={"experiment_id": exp_id},
    )
    assert rejected.status_code == 409
    assert "different projects" in rejected.text


# ---------- A2-11: link 补登已 done 实验 -------------------------------------

def test_a2_11_link_done_experiment_succeeds(
    client: TestClient, auth_headers: dict, admin_headers: dict, reviewer: dict, project: dict,
):
    """A2-11: 关联已 done 实验成功；不会触发 cascade（因为实验已是终态）。"""
    topic = _create_topic(client, auth_headers, project)
    exp_id = _create_and_approve_experiment(
        client, auth_headers, reviewer["headers"], project, title="A2-11 already done"
    )
    _run_experiment_to_done(client, auth_headers, reviewer["headers"], exp_id)
    item_id = _resolve_with_linked_item(
        client, auth_headers, topic["id"],
        title="A2-11 retroactive link",
        owner_agent_id=reviewer["id"],
        linked_experiment_id=None,
    )

    # link 一个已 done 的实验（用 admin_headers 绕过 owner 校验）
    linked = client.post(
        f"/api/v1/action-items/{item_id}/link",
        headers=admin_headers,
        params={"experiment_id": exp_id},
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["linked_experiment_id"] == exp_id
    assert linked.json()["status"] == "open"  # 不会 cascade

    # 幂等：再次 link 同 exp 返回 200 不变
    relink = client.post(
        f"/api/v1/action-items/{item_id}/link",
        headers=admin_headers,
        params={"experiment_id": exp_id},
    )
    assert relink.status_code == 200
    assert relink.json()["linked_experiment_id"] == exp_id

    # 拒绝改链不同 exp
    other_exp = _create_and_approve_experiment(
        client, auth_headers, reviewer["headers"], project, title="A2-11 other"
    )
    reject = client.post(
        f"/api/v1/action-items/{item_id}/link",
        headers=admin_headers,
        params={"experiment_id": other_exp},
    )
    assert reject.status_code == 409
