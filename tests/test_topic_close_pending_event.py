"""experiment-done-topic-close-event（f49de698）：accept-result 事件桥测试。

七组（plan v2 测试面）：
1. accept 触发（FS 话题 recipient/文案正确）
2. reject 不触发
3. executor 分离文案带 executor 名 / 同人不带
4. 白名单包含断言（wakeable 通道，不新增 work kind）
5. DB 话题降级
6. topic_id 为空跳过
7. 事务绑定（emit_kind commit=False，随 accept 事务）
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from map_fs import topic_id_for_slug, write_topic_index
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Agent, Experiment, ExperimentPhase, Notification, Project
from server.domain.schemas import ExperimentResultDecision
from server.services import phase_service
from server.services.notification_service import WAKEABLE_NOTIFICATION_EVENTS


def _setup(
    db: Session,
    tmp_path: Path,
    *,
    slug: str = "ev-demo",
    with_topic: bool = True,
    with_executor: bool = False,
):
    """Project（workspace 可达）+ host/reviewer(/executor) agents + 实验。"""
    project = Project(
        project_key=f"ev-{uuid.uuid4().hex[:8]}",
        name="Event bridge",
        workspace_path=str(tmp_path),
    )
    db.add(project)
    db.flush()

    def _agent(name: str) -> Agent:
        # Agent.name 全局唯一：同测试内二次 _setup 复用既有行（get_or_create）
        existing = db.scalar(select(Agent).where(Agent.name == name))
        if existing is not None:
            return existing
        agent = Agent(
            name=name, api_token_hash=f"hash-{name}", role="agent", project_id=project.id
        )
        db.add(agent)
        db.flush()
        return agent

    host = _agent("multi-agent-platform-host")
    reviewer = _agent("multi-agents-platform-reviewer")
    executor_id = None
    if with_executor:
        executor_id = _agent("multi-agent-platform-participant").id

    topic_id = None
    if with_topic:
        write_topic_index(tmp_path, slug, title="EV", creator="host")
        topic_id = topic_id_for_slug(slug)

    experiment = Experiment(
        project_id=project.id,
        creator_agent_id=host.id,
        title="事件桥实验",
        phase=ExperimentPhase.result_review,
        executor_agent_id=executor_id,
        topic_id=topic_id,
    )
    db.add(experiment)
    db.flush()
    return experiment, host, reviewer


def _notifications(db: Session, experiment: Experiment) -> list[Notification]:
    return list(
        db.scalars(
            select(Notification).where(
                Notification.project_id == experiment.project_id,
                Notification.event == "topic.close_pending",
            )
        )
    )


def _accept(db: Session, experiment: Experiment, reviewer: Agent) -> None:
    phase_service.accept_result(
        db,
        experiment.id,
        reviewer,
        ExperimentResultDecision(summary="通过", content_md="result", verdict_file=None),
    )


def test_accept_emits_close_pending_for_fs_topic(db_session: Session, tmp_path: Path) -> None:
    """B1：accept-result → FS 话题 creator（host persona）收到 wakeable 通知。"""
    experiment, host, reviewer = _setup(db_session, tmp_path)
    _accept(db_session, experiment, reviewer)

    rows = _notifications(db_session, experiment)
    assert len(rows) == 1
    assert rows[0].recipient_agent_id == host.id
    assert rows[0].category.value == "wakeable"
    assert "ev-demo" in rows[0].summary
    assert str(experiment.id)[:8] in rows[0].summary
    # 文案衔接 close 门禁（D4）
    assert "action-items" in rows[0].summary
    assert "close" in rows[0].summary


def test_reject_result_does_not_emit(db_session: Session, tmp_path: Path) -> None:
    """B2：reject-result 不触发事件桥。"""
    experiment, _host, reviewer = _setup(db_session, tmp_path)
    phase_service.reject_result(
        db_session,
        experiment.id,
        reviewer,
        ExperimentResultDecision(summary="驳回", content_md="result", verdict_file=None),
    )
    assert _notifications(db_session, experiment) == []
    assert experiment.phase == ExperimentPhase.running


def test_executor_separation_carries_executor_name(
    db_session: Session, tmp_path: Path
) -> None:
    """B3：executor 与 creator 分离 → 文案带 executor 名；同人不带。"""
    experiment, _host, reviewer = _setup(
        db_session, tmp_path, slug="sep-demo", with_executor=True
    )
    _accept(db_session, experiment, reviewer)
    summary = _notifications(db_session, experiment)[0].summary
    assert "multi-agent-platform-participant" in summary

    # 同人（executor=None）不带 executor 片段——同 project 第二个实验
    # （persona 解析按 Agent.project_id，复用同一 project 才能路由到 host）
    write_topic_index(tmp_path, "solo-demo", title="Solo", creator="host")
    experiment2 = Experiment(
        project_id=experiment.project_id,
        creator_agent_id=experiment.creator_agent_id,
        title="事件桥实验2",
        phase=ExperimentPhase.result_review,
        topic_id=topic_id_for_slug("solo-demo"),
    )
    db_session.add(experiment2)
    db_session.flush()
    _accept(db_session, experiment2, reviewer)
    solo_rows = [
        r for r in _notifications(db_session, experiment2) if r.target_id == experiment2.topic_id
    ]
    assert len(solo_rows) == 1
    assert "executor:" not in solo_rows[0].summary


def test_event_in_wakeable_whitelist() -> None:
    """B4/B1：event 显式声明在白名单（wakeable 通道），且不新增 work kind。"""
    assert "topic.close_pending" in WAKEABLE_NOTIFICATION_EVENTS
    from server.services.work_kinds import WORK_ITEM_KINDS

    assert "topic_close_pending" not in {spec.kind for spec in WORK_ITEM_KINDS}


def test_bootstrap_named_host_executor_omits_fragment(
    db_session: Session, tmp_path: Path
) -> None:
    """B3 residual：executor 与 creator 同为 host persona，即使 long name 不是
    ``multi-agent-platform-host``，文案也不带 executor 片段。"""
    project = Project(
        project_key=f"ev-{uuid.uuid4().hex[:8]}",
        name="Event bridge",
        workspace_path=str(tmp_path),
    )
    db_session.add(project)
    db_session.flush()

    def _mk(name: str) -> Agent:
        agent = Agent(
            name=name, api_token_hash=f"hash-{name}", role="agent", project_id=project.id
        )
        db_session.add(agent)
        db_session.flush()
        return agent

    host = _mk("acme-host")
    reviewer = _mk("acme-reviewer")
    write_topic_index(tmp_path, "acme-demo", title="EV", creator="host")
    experiment = Experiment(
        project_id=project.id,
        creator_agent_id=host.id,
        title="事件桥实验",
        phase=ExperimentPhase.result_review,
        executor_agent_id=host.id,
        topic_id=topic_id_for_slug("acme-demo"),
    )
    db_session.add(experiment)
    db_session.flush()

    _accept(db_session, experiment, reviewer)
    rows = _notifications(db_session, experiment)
    assert len(rows) == 1
    assert rows[0].recipient_agent_id == host.id
    assert "executor:" not in rows[0].summary


def test_db_topic_falls_back_to_creator_persona(db_session: Session, tmp_path: Path) -> None:
    """B5 降级：DB 存量话题（无 FS 文件夹）→ creator agent 名反推 persona，不 crash。"""
    experiment, host, reviewer = _setup(db_session, tmp_path)
    from server.domain.models import Topic, TopicStatus

    db_topic = Topic(
        project_id=experiment.project_id,
        title="DB 存量话题",
        status=TopicStatus.open,
        creator_agent_id=host.id,
    )
    db_session.add(db_topic)
    db_session.flush()
    experiment.topic_id = db_topic.id
    db_session.flush()

    _accept(db_session, experiment, reviewer)
    rows = _notifications(db_session, experiment)
    assert len(rows) == 1
    assert rows[0].recipient_agent_id == host.id


def test_no_topic_experiment_skips_notification(db_session: Session, tmp_path: Path) -> None:
    """B5（v2）：topic_id 为空的无话题实验 → 跳过通知，accept 主流程不受影响。"""
    experiment, _host, reviewer = _setup(db_session, tmp_path, with_topic=False)
    _accept(db_session, experiment, reviewer)
    assert experiment.phase == ExperimentPhase.done
    assert _notifications(db_session, experiment) == []


def test_notification_binds_to_accept_transaction(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B6：emit_kind 以 commit=False 调用——通知 rows 与 accept_result 同事务
    （enqueue_for_agents 只 flush，accept 回滚则通知随之消失）。"""
    experiment, _host, reviewer = _setup(db_session, tmp_path)
    calls: list[dict] = []
    from server.services import notification_service as _notif_svc

    original = _notif_svc.emit_kind

    def _spy(db, **kwargs):
        calls.append(kwargs)
        return original(db, **kwargs)

    monkeypatch.setattr(_notif_svc, "emit_kind", _spy)
    _accept(db_session, experiment, reviewer)

    assert len(calls) == 1
    assert calls[0]["event"] == "topic.close_pending"
    assert calls[0]["commit"] is False  # 同事务绑定（B6）


# --- plan-mode-direct-execution-productization 复核 -------------------
# direct 模式下 complete 跳过 reviewer 直接进 done，但事件桥（topic.close_pending
# 唤醒 host 收尾话题）必须与 standard accept 行为一致，否则直接派生的实验会卡在
# ready 等不到收尾。completer=executor(participant) 时 executor 与 creator
# 分离，文案带 executor 名片段；completer 与 creator 同人（自执行）则不带。


def _setup_direct_running(
    db: Session,
    tmp_path: Path,
    *,
    slug: str = "direct-ev",
    with_executor: bool = True,
):
    """direct 实验：running 阶段待 complete，host creator + 可选 executor。"""
    project = Project(
        project_key=f"ev-{uuid.uuid4().hex[:8]}",
        name="Event bridge direct",
        workspace_path=str(tmp_path),
    )
    db.add(project)
    db.flush()

    def _agent(name: str) -> Agent:
        existing = db.scalar(select(Agent).where(Agent.name == name))
        if existing is not None:
            return existing
        a = Agent(
            name=name, api_token_hash=f"hash-{name}", role="agent", project_id=project.id
        )
        db.add(a)
        db.flush()
        return a

    host = _agent("multi-agent-platform-host")
    executor = _agent("multi-agent-platform-participant") if with_executor else host

    write_topic_index(tmp_path, slug, title="Direct EV", creator="host")
    topic_id = topic_id_for_slug(slug)

    experiment = Experiment(
        project_id=project.id,
        creator_agent_id=host.id,
        executor_agent_id=executor.id,
        title="direct 事件桥实验",
        phase=ExperimentPhase.running,
        mode="direct",
        topic_id=topic_id,
        current_plan_version=1,
    )
    db.add(experiment)
    db.flush()
    return experiment, host, executor


def _direct_complete(db: Session, experiment: Experiment, actor: Agent) -> None:
    from server.domain.schemas import ExperimentComplete

    phase_service.complete_experiment(
        db,
        experiment.id,
        actor,
        ExperimentComplete(summary="完成：direct", content_md="done"),
    )


def test_direct_complete_emits_close_pending(db_session: Session, tmp_path: Path) -> None:
    """direct 模式下 complete 直接 done 也必须发 topic.close_pending 唤醒 host
    收尾话题（与 standard accept 行为对齐）。"""
    experiment, host, executor = _setup_direct_running(
        db_session, tmp_path, slug="direct-ev", with_executor=True
    )
    _direct_complete(db_session, experiment, executor)
    assert experiment.phase == ExperimentPhase.done

    rows = _notifications(db_session, experiment)
    assert len(rows) == 1
    assert rows[0].recipient_agent_id == host.id
    assert rows[0].category.value == "wakeable"
    assert "direct-ev" in rows[0].summary
    assert str(experiment.id)[:8] in rows[0].summary
    # 文案衔接 close 门禁（D4）
    assert "action-items" in rows[0].summary
    assert "close" in rows[0].summary
    # host-review round2 #5：文案统一为「实验已完成」（直接/done 与
    # standard/accept 同一字符串，不再说"已验收"误导走 standard 审批路径）。
    assert "实验已完成" in rows[0].summary, (
        f"close_pending summary must use 实验已完成; got: {rows[0].summary!r}"
    )


def test_direct_complete_separation_carries_executor_name(
    db_session: Session, tmp_path: Path
) -> None:
    """direct completer=executor（与 creator 分离）→ 文案带 executor 片段。"""
    experiment, _host, executor = _setup_direct_running(
        db_session, tmp_path, slug="direct-sep", with_executor=True
    )
    _direct_complete(db_session, experiment, executor)
    summary = _notifications(db_session, experiment)[0].summary
    assert "multi-agent-platform-participant" in summary


def test_direct_complete_binds_to_same_transaction(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """direct complete 的事件桥与 complete 自身同事务（emit_kind commit=False），
    回滚则通知随之消失——与 standard accept_result 的 B6 一致。"""
    experiment, _host, executor = _setup_direct_running(
        db_session, tmp_path, slug="direct-tx", with_executor=True
    )
    calls: list[dict] = []
    from server.services import notification_service as _notif_svc

    original = _notif_svc.emit_kind

    def _spy(db, **kwargs):
        calls.append(kwargs)
        return original(db, **kwargs)

    monkeypatch.setattr(_notif_svc, "emit_kind", _spy)
    _direct_complete(db_session, experiment, executor)

    assert any(c["event"] == "topic.close_pending" for c in calls)
    close_pending_call = next(c for c in calls if c["event"] == "topic.close_pending")
    assert close_pending_call["commit"] is False


def test_direct_complete_no_topic_skips_notification(
    db_session: Session, tmp_path: Path
) -> None:
    """direct 无话题实验 → 跳过通知，complete 主流程不受影响（与 B5 v2 对齐）。

    host agent 必须 project-scoped：用 ``<project_key>-host`` 命名，不复用
    全局 ``multi-agent-platform-host``——后者被 _resolve_persona_agent_ids
    当作默认 host persona 路由（与本测试 project 无关），会让通知 recipient
    解析跑偏到别的 project。
    """
    project_key = f"ev-{uuid.uuid4().hex[:8]}"
    project = Project(
        project_key=project_key,
        name="No topic",
        workspace_path=str(tmp_path),
    )
    db_session.add(project)
    db_session.flush()

    # project-scoped host（自执行场景下 creator == executor == host.id）。
    host = Agent(
        name=f"{project_key}-host",
        api_token_hash="h",
        role="agent",
        project_id=project.id,
    )
    db_session.add(host)
    db_session.flush()

    experiment = Experiment(
        project_id=project.id,
        creator_agent_id=host.id,
        executor_agent_id=host.id,  # 自执行（carve-out：不会进 executor_assignments）
        title="no-topic-direct",
        phase=ExperimentPhase.running,
        mode="direct",
        topic_id=None,
        current_plan_version=1,
    )
    db_session.add(experiment)
    db_session.flush()

    _direct_complete(db_session, experiment, host)
    assert experiment.phase == ExperimentPhase.done
    assert _notifications(db_session, experiment) == []
