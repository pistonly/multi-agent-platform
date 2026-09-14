"""A2-1（实验 plan-db-content-retirement）：``sync migrate`` 的 plan 类 kind。

migration manifest scan 为每个未删除实验的**当前版** PlanVersion 登记一条
``kind="plan"`` item（迁移候选簿记）。与 topic kind 同例（M2 先例：scan 先
登记、apply 路径后接）——apply 语义（materialize 物化 + content_md stub 化）
归 A2-2，本文件只钉 scan 面：

1. 有当前版 PlanVersion 的实验 → 各产 1 条 plan item；by_kind 计数正确。
2. 只有当前版进 scan（历史版本不重复登记）。
3. 幂等：重跑 scan 不重复建行（idempotency_key UNIQUE）。
4. plan 修订（bump current_plan_version + 新正文）→ content_hash 变 →
   新 item 行，旧行不动。
5. dry execute 把 plan item 记 skipped（可被 --apply 捡回，与 topic/
   experiment 同语义）；apply 下 plan kind 暂无 apply 路径，会以
   unsupported 失败进 retry 留痕——A2-2 接线前的已登记中间态，与 topic
   kind 的既有先例（test_execute_pending_apply_failure_tags_stale_code）
   完全一致。

范围声明：kind 空间是 migration manifest 的簿记维度（topic/experiment/
plan），与 waker 的 WORK_ITEM_KINDS 正交——plan item 不产生 work item，
不注册 work_kinds registry（注册会被 test_work_kind_registry_consistency
的产 kind 面守卫拒绝，语义亦错）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from map_types.enums import AgentRole, ExperimentMode, ExperimentPhase
from sqlalchemy import select

from server.domain.models import (
    Agent,
    Experiment,
    MigrationManifestItem,
    PlanVersion,
    Project,
)
from server.services import migration_manifest_service as svc


def _make_agent(db_session, *, project_id):
    agent = Agent(
        id=uuid.uuid4(),
        name=f"plankind-{uuid.uuid4().hex[:8]}",
        api_token_hash="h",
        api_token_prefix="tt",
        api_token_sha256=uuid.uuid4().hex,
        role=AgentRole.agent,
        project_id=project_id,
    )
    db_session.add(agent)
    db_session.flush()
    return agent


def _make_project(db_session):
    project = Project(
        id=uuid.uuid4(),
        project_key=f"pk-{uuid.uuid4().hex[:8]}",
        name="plan kind test",
        workspace_path="/tmp/pk-test",
        content_root="map",
    )
    db_session.add(project)
    db_session.flush()
    return project


def _make_experiment(db_session, *, project_id, creator_id, current_plan_version=1):
    experiment = Experiment(
        id=uuid.uuid4(),
        title="exp",
        description="d",
        phase=ExperimentPhase.draft,
        mode=ExperimentMode.standard,
        project_id=project_id,
        creator_agent_id=creator_id,
        current_plan_version=current_plan_version,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(experiment)
    db_session.flush()
    return experiment


def _make_plan(db_session, *, experiment_id, author_id, version, content="plan body"):
    plan = PlanVersion(
        id=uuid.uuid4(),
        experiment_id=experiment_id,
        version=version,
        content_md=content,
        author_agent_id=author_id,
    )
    db_session.add(plan)
    db_session.flush()
    return plan


def _plan_items(db_session, *, project_id):
    return list(
        db_session.scalars(
            select(MigrationManifestItem).where(
                MigrationManifestItem.project_id == project_id,
                MigrationManifestItem.kind == "plan",
            )
        )
    )


def test_scan_registers_plan_item_per_current_plan_version(db_session):
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    exp = _make_experiment(db_session, project_id=project.id, creator_id=agent.id)
    _make_plan(db_session, experiment_id=exp.id, author_id=agent.id, version=1)

    report = svc.scan_project(db_session, project_id=project.id)

    assert report.by_kind == {"topic": 0, "experiment": 1, "plan": 1}
    assert report.scanned == 2 and report.inserted == 2
    items = _plan_items(db_session, project_id=project.id)
    assert len(items) == 1
    item = items[0]
    assert item.slug == str(exp.id)
    assert item.status == "pending"


def test_scan_registers_only_current_version(db_session):
    """历史版本不重复登记：v1 已归档、current=v2 → 只有 v2 的正文进 scan。"""
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    exp = _make_experiment(
        db_session, project_id=project.id, creator_id=agent.id, current_plan_version=2
    )
    _make_plan(db_session, experiment_id=exp.id, author_id=agent.id, version=1, content="v1")
    _make_plan(db_session, experiment_id=exp.id, author_id=agent.id, version=2, content="v2")

    report = svc.scan_project(db_session, project_id=project.id)

    assert report.by_kind["plan"] == 1
    items = _plan_items(db_session, project_id=project.id)
    assert len(items) == 1
    # 用 v2 payload 的 hash 一致才算登记了当前版
    expected = svc._compute_content_hash(
        "plan",
        {"experiment_id": str(exp.id), "version": 2, "content_md": "v2"},
    )
    assert items[0].content_hash == expected


def test_scan_plan_item_idempotent_on_rerun(db_session):
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    exp = _make_experiment(db_session, project_id=project.id, creator_id=agent.id)
    _make_plan(db_session, experiment_id=exp.id, author_id=agent.id, version=1)

    first = svc.scan_project(db_session, project_id=project.id)
    svc.scan_project(db_session, project_id=project.id)  # 重跑：幂等，不新增行

    assert first.by_kind["plan"] == 1
    plan_items = _plan_items(db_session, project_id=project.id)
    assert len(plan_items) == 1  # idempotency_key UNIQUE：重跑不重复建行


def test_plan_revision_creates_new_item(db_session):
    """bump current_plan_version + 新正文 → hash 变 → 新 pending 行，旧行留原状。"""
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    exp = _make_experiment(db_session, project_id=project.id, creator_id=agent.id)
    _make_plan(db_session, experiment_id=exp.id, author_id=agent.id, version=1, content="v1")
    svc.scan_project(db_session, project_id=project.id)

    _make_plan(db_session, experiment_id=exp.id, author_id=agent.id, version=2, content="v2")
    exp.current_plan_version = 2
    db_session.flush()

    second = svc.scan_project(db_session, project_id=project.id)
    # by_kind 计的是「本轮扫描集」= 当前版（v2）→ 1（旧 v1 不在扫描集内）。
    # 意图落在 plan 行本身：hash 变 → 幂等键变 → 新 pending 行，旧行不动。
    assert second.by_kind["plan"] == 1
    plan_items = _plan_items(db_session, project_id=project.id)
    assert len(plan_items) == 2  # 旧 v1 行不动 + 新 v2 行
    statuses = sorted(i.status for i in plan_items)
    assert statuses == ["pending", "pending"]
    hashes = {i.content_hash for i in plan_items}
    assert len(hashes) == 2  # v1/v2 正文不同 → 两条不同 hash，未互相覆盖


def test_execute_dry_marks_plan_item_skipped(db_session):
    """dry execute 把 plan item 记 skipped（可被 --apply 捡回）——不伪造 applied。"""
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    exp = _make_experiment(db_session, project_id=project.id, creator_id=agent.id)
    _make_plan(db_session, experiment_id=exp.id, author_id=agent.id, version=1)
    svc.scan_project(db_session, project_id=project.id)

    report = svc.execute_pending(
        db_session, project=project, agent=agent, apply=False, limit=50
    )
    assert report["failed"] == 0
    item = _plan_items(db_session, project_id=project.id)[0]
    assert item.status == "skipped"


def test_execute_apply_plan_fails_as_unsupported_until_a2_2(db_session):
    """A2-2 接线前：plan kind 与 topic kind 同例——apply 无路径 → 失败留痕、
    attempts 计数、回 pending（retry budget 内）。证明登记是安全的只读
    中间态，不会污染已支持 kind 的 execute 流。"""
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    exp = _make_experiment(db_session, project_id=project.id, creator_id=agent.id)
    _make_plan(db_session, experiment_id=exp.id, author_id=agent.id, version=1)
    svc.scan_project(db_session, project_id=project.id)

    report = svc.execute_pending(
        db_session, project=project, agent=agent, apply=True, limit=50
    )
    # 全局 failed=2：本 fixture 的实验无 FS 投影 → experiment item 也失败
    # （既有先例），plan item 因 unsupported kind 失败。两者均不污染
    # processed（apply 全失败 → processed=0），证明 plan 登记是安全只读
    # 中间态。本用例只钉 plan item 的留痕细节。
    assert report["processed"] == 0
    assert report["failed"] == 2
    item = _plan_items(db_session, project_id=project.id)[0]
    assert item.attempts == 1
    assert item.status == "pending"
    assert "unsupported manifest kind: plan" in (item.last_error or "")
