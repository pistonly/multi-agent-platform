"""A2-2（实验 plan-db-content-retirement）：``sync migrate verify`` 的 plan 域对账。

``verify_project`` 新增附加 ``plan_domain`` 段——逐实验当前版 PlanVersion
比对 DB ``content_md`` ↔ FS ``plan.md``：

- **verified**：字节一致；或 DB 已是 stub（``See file:`` / ``<!-- slim create:``）
  指向 FS 现存文件（A2-2 stub 化 / slim create 完成态）。
- **mismatch**：两侧都存在但字节不同（需 materialize --force 覆盖 FS）。
- **missing**：无 ``plan_file_path``，或指向的 FS 文件不存在（需先 materialize）。

**相位门禁（关键安全属性）**：flag ``plan_db_content_retired`` OFF 时 plan 域
问题按**信息**处理——顶层 blocking 计数（``mismatch_count`` / ``missing_count``）
**不变**，plan 域问题只在 ``plan_domain`` 段列出、``blocking=False``。这保证
「flag off 行为与现状一致」（验收 A1）：所有 FS 制品尚未物化的存量项目不会
被误报红。flag ON 时 ``plan_domain.blocking=True``，caller（host 编排 / CLI）
据此把 plan 域 missing/mismatch 视为切 FS 权威前的阻塞。

destructive 的 content_md stub 化本身归 destructive 运维动作（host 在 A3-1 读
路径就绪 + flag flip 后触发），不在本对账测试范围——本文件只钉对账判据与
flag 门禁。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from map_types.enums import AgentRole, ExperimentMode, ExperimentPhase

from server.domain.models import (
    Agent,
    Experiment,
    PlanVersion,
    Project,
    ProjectFeatureFlag,
)
from server.services import migration_manifest_service as svc


def _make_project(db_session, *, workspace_path):
    project = Project(
        id=uuid.uuid4(),
        project_key=f"pv-{uuid.uuid4().hex[:8]}",
        name="plan verify test",
        workspace_path=str(workspace_path),
        content_root="map",
    )
    db_session.add(project)
    db_session.flush()
    return project


def _make_agent(db_session, *, project_id):
    agent = Agent(
        id=uuid.uuid4(),
        name=f"planverify-{uuid.uuid4().hex[:8]}",
        api_token_hash="h",
        api_token_prefix="tt",
        api_token_sha256=uuid.uuid4().hex,
        role=AgentRole.agent,
        project_id=project_id,
    )
    db_session.add(agent)
    db_session.flush()
    return agent


def _make_experiment(db_session, *, project_id, creator_id, plan_file_path=None):
    now = datetime.now(timezone.utc)
    experiment = Experiment(
        id=uuid.uuid4(),
        title="exp",
        description="d",
        phase=ExperimentPhase.draft,
        mode=ExperimentMode.standard,
        project_id=project_id,
        creator_agent_id=creator_id,
        current_plan_version=1,
        plan_file_path=plan_file_path,
        created_at=now,
        updated_at=now,
    )
    db_session.add(experiment)
    db_session.flush()
    return experiment


def _make_plan(db_session, *, experiment_id, author_id, version, content):
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


def _write_fs_plan(tmp_path, rel_path, text):
    from pathlib import Path

    full = Path(tmp_path) / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(text, encoding="utf-8")
    return full


def _set_flag(db_session, *, project_id, actor_id, value):
    db_session.add(
        ProjectFeatureFlag(
            project_id=project_id,
            flag_key="plan_db_content_retired",
            flag_value=value,
            set_by_agent_id=actor_id,
            reason="test",
        )
    )
    db_session.flush()


# verify 的 experiment 域会读 fs_experiments_view；本文件只关心 plan 域，
# 用一个恒空的 view 让 experiment 域全部落 missing（与 plan 域计数解耦）。
def _empty_view(monkeypatch):
    from server.services import fs_source_service as fss

    monkeypatch.setattr(fss, "fs_experiments_view", lambda db, project: [])


# ---------------------------------------------------------------------------


def test_plan_verified_when_fs_bytes_match(db_session, tmp_path, monkeypatch):
    _empty_view(monkeypatch)
    project = _make_project(db_session, workspace_path=tmp_path)
    agent = _make_agent(db_session, project_id=project.id)
    rel = "map/experiments/pv-ok/plan.md"
    body = "# plan\n正文"
    exp = _make_experiment(
        db_session, project_id=project.id, creator_id=agent.id, plan_file_path=rel
    )
    _write_fs_plan(tmp_path, rel, body)
    _make_plan(
        db_session, experiment_id=exp.id, author_id=agent.id, version=1, content=body
    )

    report = svc.verify_project(db_session, project=project)

    pd = report["plan_domain"]
    assert pd["verified_count"] == 1
    assert pd["mismatch_count"] == 0
    assert pd["missing_count"] == 0
    assert pd["verified"][0]["form"] == "full"


def test_plan_verified_when_db_is_stub(db_session, tmp_path, monkeypatch):
    _empty_view(monkeypatch)
    project = _make_project(db_session, workspace_path=tmp_path)
    agent = _make_agent(db_session, project_id=project.id)
    rel = "map/experiments/pv-stub/plan.md"
    exp = _make_experiment(
        db_session, project_id=project.id, creator_id=agent.id, plan_file_path=rel
    )
    _write_fs_plan(tmp_path, rel, "# 真正文在 FS")
    # A2-2 stub 化 / slim create 完成态：DB 存自描述 stub 指向同一路径。
    _make_plan(
        db_session,
        experiment_id=exp.id,
        author_id=agent.id,
        version=1,
        content=f"See file: {rel}",
    )

    report = svc.verify_project(db_session, project=project)
    pd = report["plan_domain"]
    assert pd["verified_count"] == 1
    assert pd["verified"][0]["form"] == "stub"


def test_plan_mismatch_when_bytes_differ(db_session, tmp_path, monkeypatch):
    _empty_view(monkeypatch)
    project = _make_project(db_session, workspace_path=tmp_path)
    agent = _make_agent(db_session, project_id=project.id)
    rel = "map/experiments/pv-drift/plan.md"
    exp = _make_experiment(
        db_session, project_id=project.id, creator_id=agent.id, plan_file_path=rel
    )
    _write_fs_plan(tmp_path, rel, "FS 旧正文")
    _make_plan(
        db_session,
        experiment_id=exp.id,
        author_id=agent.id,
        version=1,
        content="DB 新正文",
    )

    report = svc.verify_project(db_session, project=project)
    pd = report["plan_domain"]
    assert pd["mismatch_count"] == 1
    assert pd["mismatched"][0]["fields"][0]["field"] == "content_md"
    # 两侧 sha256 都列出，便于定位漂移方向
    assert pd["mismatched"][0]["fields"][0]["db_sha256"] != pd["mismatched"][0][
        "fields"
    ][0]["fs_sha256"]


def test_plan_missing_when_file_absent(db_session, tmp_path, monkeypatch):
    _empty_view(monkeypatch)
    project = _make_project(db_session, workspace_path=tmp_path)
    agent = _make_agent(db_session, project_id=project.id)
    rel = "map/experiments/pv-absent/plan.md"
    exp = _make_experiment(
        db_session, project_id=project.id, creator_id=agent.id, plan_file_path=rel
    )
    # FS plan.md 不存在
    _make_plan(
        db_session,
        experiment_id=exp.id,
        author_id=agent.id,
        version=1,
        content="DB-only plan，未物化",
    )

    report = svc.verify_project(db_session, project=project)
    pd = report["plan_domain"]
    assert pd["missing_count"] == 1
    assert "materialize" in pd["missing"][0]["reason"]


def test_plan_missing_when_no_plan_file_path(db_session, tmp_path, monkeypatch):
    _empty_view(monkeypatch)
    project = _make_project(db_session, workspace_path=tmp_path)
    agent = _make_agent(db_session, project_id=project.id)
    exp = _make_experiment(
        db_session, project_id=project.id, creator_id=agent.id, plan_file_path=None
    )
    _make_plan(
        db_session,
        experiment_id=exp.id,
        author_id=agent.id,
        version=1,
        content="纯 DB 内联，无 plan_file_path",
    )

    report = svc.verify_project(db_session, project=project)
    pd = report["plan_domain"]
    assert pd["missing_count"] == 1
    assert "plan_file_path" in pd["missing"][0]["reason"]


def test_flag_off_plan_issues_not_blocking(db_session, tmp_path, monkeypatch):
    """关键安全属性：flag OFF 时 plan 域 missing/mismatch 不进顶层 blocking
    计数（验收 A1：flag off 行为与现状一致，存量未物化项目不误报红）。"""
    _empty_view(monkeypatch)
    project = _make_project(db_session, workspace_path=tmp_path)
    agent = _make_agent(db_session, project_id=project.id)
    # 一个 missing（无文件）+ 一个 mismatch（字节不同）
    rel_missing = "map/experiments/pv-m1/plan.md"
    exp1 = _make_experiment(
        db_session,
        project_id=project.id,
        creator_id=agent.id,
        plan_file_path=rel_missing,
    )
    _make_plan(
        db_session, experiment_id=exp1.id, author_id=agent.id, version=1, content="x"
    )
    rel_drift = "map/experiments/pv-m2/plan.md"
    exp2 = _make_experiment(
        db_session, project_id=project.id, creator_id=agent.id, plan_file_path=rel_drift
    )
    _write_fs_plan(tmp_path, rel_drift, "FS")
    _make_plan(
        db_session, experiment_id=exp2.id, author_id=agent.id, version=1, content="DB"
    )

    # flag 未 set → OFF
    report = svc.verify_project(db_session, project=project)
    pd = report["plan_domain"]
    assert pd["flag_on"] is False
    assert pd["blocking"] is False
    assert pd["mismatch_count"] == 1 and pd["missing_count"] == 1
    # 顶层 blocking 计数不含 plan 域（experiment 域本 fixture 全 missing，
    # 但那是 fs_experiments_view 空导致的既有 experiment 域行为，与 plan 域无关）
    # 关键断言：plan 域问题没有把顶层数字抬高——顶层只数 experiment 域。
    # experiment 域 = 2 个实验都 missing（view 空）。
    assert report["missing_count"] == 2  # 纯 experiment 域
    assert report["mismatch_count"] == 0  # plan mismatch 未混入


def test_flag_on_marks_plan_domain_blocking(db_session, tmp_path, monkeypatch):
    """flag ON 时 plan 域 blocking=True——切 FS 权威前必须先消解 plan 域问题。
    顶层 experiment 域计数仍保持原语义（plan 域单独成段，由 caller 合并检查）。"""
    _empty_view(monkeypatch)
    project = _make_project(db_session, workspace_path=tmp_path)
    agent = _make_agent(db_session, project_id=project.id)
    rel = "map/experiments/pv-on/plan.md"
    exp = _make_experiment(
        db_session, project_id=project.id, creator_id=agent.id, plan_file_path=rel
    )
    _make_plan(
        db_session, experiment_id=exp.id, author_id=agent.id, version=1, content="x"
    )  # file absent → missing
    _set_flag(db_session, project_id=project.id, actor_id=agent.id, value="on")

    report = svc.verify_project(db_session, project=project)
    pd = report["plan_domain"]
    assert pd["flag_on"] is True
    assert pd["blocking"] is True
    assert pd["missing_count"] == 1


def test_experiment_without_current_plan_excluded(db_session, tmp_path, monkeypatch):
    """无当前版 PlanVersion 的实验不入 plan 域（current_plan_version=0 或无行）。"""
    _empty_view(monkeypatch)
    project = _make_project(db_session, workspace_path=tmp_path)
    agent = _make_agent(db_session, project_id=project.id)
    exp = _make_experiment(
        db_session, project_id=project.id, creator_id=agent.id, plan_file_path=None
    )
    exp.current_plan_version = 0  # 无当前版
    db_session.flush()
    # 不建 PlanVersion 行

    report = svc.verify_project(db_session, project=project)
    pd = report["plan_domain"]
    assert pd["verified_count"] == 0
    assert pd["mismatch_count"] == 0
    assert pd["missing_count"] == 0
