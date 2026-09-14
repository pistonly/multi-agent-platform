"""A3-1（实验 plan-db-content-retirement）：flag on 读路径 fail-closed + FS fallback。

``plan_service.resolve_plan_content`` 是所有 plan 正文消费方的唯一收口：

- flag ``plan_db_content_retired`` OFF（默认）→ 恒等返回 DB ``content_md``
  （验收 A1 读路径锚点：逐字节不变）。
- flag ON：
  - 当前版：从 FS ``plan.md`` 读（DB 存全文或 stub 都无视——FS 是权威）。
  - stub（任意版本，``See file:`` / ``<!-- slim create:`` 开头）：从 FS 解引用。
  - 历史版全文：DB 原样返回（A2-2 stub 化只作用于当前版，FS 无历史镜像）。
  - ``plan.md`` 缺失 / 无 ``plan_file_path`` / 读失败 → **fail-closed**
    ``ConflictError``(409, error=``plan_md_missing``)，文案指向
    ``map experiment plan materialize``（验收 A3-1）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from map_types.enums import AgentRole, ExperimentMode, ExperimentPhase
from sqlalchemy.exc import IntegrityError

from server.domain.models import (
    Agent,
    Experiment,
    PlanVersion,
    Project,
    ProjectFeatureFlag,
)
from server.services import plan_service
from server.services.errors import ConflictError


def _make_project(db_session, *, workspace_path):
    project = Project(
        id=uuid.uuid4(),
        project_key=f"pr-{uuid.uuid4().hex[:8]}",
        name="plan read test",
        workspace_path=str(workspace_path),
        content_root="map",
    )
    db_session.add(project)
    db_session.flush()
    return project


def _make_agent(db_session, *, project_id):
    agent = Agent(
        id=uuid.uuid4(),
        name=f"planread-{uuid.uuid4().hex[:8]}",
        api_token_hash="h",
        api_token_prefix="tt",
        api_token_sha256=uuid.uuid4().hex,
        role=AgentRole.agent,
        project_id=project_id,
    )
    db_session.add(agent)
    db_session.flush()
    return agent


def _make_experiment(db_session, *, project, creator_id, plan_file_path=None,
                     current_plan_version=1):
    now = datetime.now(timezone.utc)
    experiment = Experiment(
        id=uuid.uuid4(),
        title="exp",
        description="d",
        phase=ExperimentPhase.running,
        mode=ExperimentMode.standard,
        project_id=project.id,
        creator_agent_id=creator_id,
        current_plan_version=current_plan_version,
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


def _set_flag(db_session, *, project, actor, value):
    try:
        db_session.add(
            ProjectFeatureFlag(
                project_id=project.id,
                flag_key="plan_db_content_retired",
                flag_value=value,
                set_by_agent_id=actor.id,
                reason="A3-1 test",
            )
        )
        db_session.flush()
    except IntegrityError:  # 同 session 重复 flip
        db_session.rollback()
        row = db_session.get(ProjectFeatureFlag, (project.id, "plan_db_content_retired"))
        row.flag_value = value


# ---------------------------------------------------------------------------


def test_flag_off_returns_db_content_verbatim(db_session, tmp_path):
    """验收 A1 读路径锚点：flag 未 set → resolve 恒等返回 DB content_md。"""
    project = _make_project(db_session, workspace_path=tmp_path)
    agent = _make_agent(db_session, project_id=project.id)
    exp = _make_experiment(
        db_session, project=project, creator_id=agent.id, plan_file_path=None
    )
    plan = _make_plan(
        db_session, experiment_id=exp.id, author_id=agent.id, version=1,
        content="DB 全文，flag off 时就是权威",
    )
    assert plan_service.resolve_plan_content(db_session, exp, plan) == plan.content_md


def test_flag_on_current_version_reads_fs(db_session, tmp_path):
    """flag on：当前版从 FS plan.md 读——DB 存全文也无视（FS 权威）。"""
    project = _make_project(db_session, workspace_path=tmp_path)
    agent = _make_agent(db_session, project_id=project.id)
    rel = "map/experiments/pr-fs/plan.md"
    exp = _make_experiment(
        db_session, project=project, creator_id=agent.id, plan_file_path=rel
    )
    plan = _make_plan(
        db_session, experiment_id=exp.id, author_id=agent.id, version=1,
        content="DB 旧全文",
    )
    fs_body = "# FS 权威正文\nflag on 后读这里"
    fs_path = tmp_path / rel
    fs_path.parent.mkdir(parents=True)
    fs_path.write_text(fs_body, encoding="utf-8")

    _set_flag(db_session, project=project, actor=agent, value="on")
    assert plan_service.resolve_plan_content(db_session, exp, plan) == fs_body


def test_flag_on_stub_dereferences_fs(db_session, tmp_path):
    """flag on：stub（See file: / slim create）从 FS 解引用。"""
    project = _make_project(db_session, workspace_path=tmp_path)
    agent = _make_agent(db_session, project_id=project.id)
    rel = "map/experiments/pr-stub/plan.md"
    exp = _make_experiment(
        db_session, project=project, creator_id=agent.id, plan_file_path=rel
    )
    # 历史版（version=1 < current=2）存 stub：A2-2 之后的极端形态，
    # stub 判据先于「历史版全文原样返回」→ 仍从 FS 解引用。
    plan = _make_plan(
        db_session, experiment_id=exp.id, author_id=agent.id, version=1,
        content=f"See file: {rel}",
    )
    exp.current_plan_version = 2
    db_session.flush()
    fs_body = "FS 上的 stub 目标正文"
    fs_path = tmp_path / rel
    fs_path.parent.mkdir(parents=True)
    fs_path.write_text(fs_body, encoding="utf-8")

    _set_flag(db_session, project=project, actor=agent, value="on")
    assert plan_service.resolve_plan_content(db_session, exp, plan) == fs_body


def test_flag_on_missing_plan_md_fails_closed(db_session, tmp_path):
    """flag on：当前版 plan.md 缺失 → 409 fail-closed，文案指向 materialize。"""
    project = _make_project(db_session, workspace_path=tmp_path)
    agent = _make_agent(db_session, project_id=project.id)
    rel = "map/experiments/pr-missing/plan.md"
    exp = _make_experiment(
        db_session, project=project, creator_id=agent.id, plan_file_path=rel
    )
    plan = _make_plan(
        db_session, experiment_id=exp.id, author_id=agent.id, version=1,
        content="DB-only，未物化",
    )
    _set_flag(db_session, project=project, actor=agent, value="on")

    with pytest.raises(ConflictError) as excinfo:
        plan_service.resolve_plan_content(db_session, exp, plan)
    msg = str(excinfo.value)
    assert "plan materialize" in msg
    assert excinfo.value.error == "plan_md_missing"


def test_flag_on_no_plan_file_path_fails_closed(db_session, tmp_path):
    """flag on：无 plan_file_path（纯内联从未物化）→ 同样 fail-closed。"""
    project = _make_project(db_session, workspace_path=tmp_path)
    agent = _make_agent(db_session, project_id=project.id)
    exp = _make_experiment(
        db_session, project=project, creator_id=agent.id, plan_file_path=None
    )
    plan = _make_plan(
        db_session, experiment_id=exp.id, author_id=agent.id, version=1,
        content="纯 DB 内联",
    )
    _set_flag(db_session, project=project, actor=agent, value="on")

    with pytest.raises(ConflictError) as excinfo:
        plan_service.resolve_plan_content(db_session, exp, plan)
    assert "无 plan_file_path" in str(excinfo.value)
    assert excinfo.value.error == "plan_md_missing"


def test_flag_on_historical_full_version_passthrough(db_session, tmp_path):
    """flag on：历史版（非当前、非 stub）DB 仍是唯一副本，原样返回。

    A2-2 stub 化只作用于当前版；FS plan.md 只镜像当前版。历史评审上下文
    （reviewer 对比 v1/v2）必须继续从 DB 读到 v1 全文。
    """
    project = _make_project(db_session, workspace_path=tmp_path)
    agent = _make_agent(db_session, project_id=project.id)
    rel = "map/experiments/pr-hist/plan.md"
    exp = _make_experiment(
        db_session, project=project, creator_id=agent.id, plan_file_path=rel,
        current_plan_version=2,
    )
    v1 = _make_plan(
        db_session, experiment_id=exp.id, author_id=agent.id, version=1,
        content="v1 历史全文",
    )
    _make_plan(
        db_session, experiment_id=exp.id, author_id=agent.id, version=2,
        content="v2 当前全文",
    )
    fs_path = tmp_path / rel
    fs_path.parent.mkdir(parents=True)
    fs_path.write_text("v2 FS 正文", encoding="utf-8")

    _set_flag(db_session, project=project, actor=agent, value="on")
    # v1：历史全文原样返回（FS 没有 v1 的镜像，读 FS 反而会拿到 v2 正文）
    assert plan_service.resolve_plan_content(db_session, exp, v1) == "v1 历史全文"


def test_get_plan_version_resolved_flag_off_identical_shape(db_session, tmp_path):
    """list/get *_resolved 在 flag off 下与裸 model_validate 完全同形（A1 锚点）。"""
    from map_types.schemas.plan import PlanVersionRead

    project = _make_project(db_session, workspace_path=tmp_path)
    agent = _make_agent(db_session, project_id=project.id)
    exp = _make_experiment(
        db_session, project=project, creator_id=agent.id, plan_file_path=None
    )
    plan = _make_plan(
        db_session, experiment_id=exp.id, author_id=agent.id, version=1,
        content="flag off 全文",
    )
    bare = PlanVersionRead.model_validate(plan)
    resolved = plan_service.get_plan_version_resolved(db_session, exp.id, 1)
    assert resolved == bare
    listed = plan_service.list_plans_resolved(db_session, exp.id)
    assert listed == [bare]


def test_list_plans_resolved_flag_on_reads_fs(db_session, tmp_path):
    """flag on：API 读面收口按版本逐条 resolve——当前版读 FS，历史版读 DB。"""
    project = _make_project(db_session, workspace_path=tmp_path)
    agent = _make_agent(db_session, project_id=project.id)
    rel = "map/experiments/pr-list/plan.md"
    exp = _make_experiment(
        db_session, project=project, creator_id=agent.id, plan_file_path=rel,
        current_plan_version=2,
    )
    _make_plan(
        db_session, experiment_id=exp.id, author_id=agent.id, version=1,
        content="v1 历史全文",
    )
    _make_plan(
        db_session, experiment_id=exp.id, author_id=agent.id, version=2,
        content="v2 DB 旧全文",
    )
    fs_path = tmp_path / rel
    fs_path.parent.mkdir(parents=True)
    fs_path.write_text("v2 FS 权威", encoding="utf-8")

    _set_flag(db_session, project=project, actor=agent, value="on")
    reads = plan_service.list_plans_resolved(db_session, exp.id)
    assert [r.version for r in reads] == [1, 2]
    assert reads[0].content_md == "v1 历史全文"  # 历史版：DB
    assert reads[1].content_md == "v2 FS 权威"  # 当前版：FS
