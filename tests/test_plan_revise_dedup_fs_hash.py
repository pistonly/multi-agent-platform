"""A1-4（实验 plan-db-content-retirement）：flag on 时 revise 去重判据
从「DB 全文等值」切换为「FS plan.md 内容哈希」（B 点修正）。

Pins evidence ``flag_on_duplicate_revise_no_bump_no_archive``：

1. flag on + DB v1 是 stub + 提交正文与 FS plan.md 字节一致 → early-return
   同一 PlanVersion（不 bump、不触发 _archive_prior_version_reviews，
   上一版 review 不被误归档）。
2. flag on + FS plan.md 与提交正文不同 → 正常 bump + 归档级联照常
   （去重不过度抑制真实修订）。
3. flag on + FS plan.md 缺失（跨机部署）→ 无法判定，回退旧等值判据：
   stub ≠ 全文 → 保守 bump（宁可多 bump，不吞真实修订）。
4. flag off（默认）：FS 制品存在与否都不影响——等值判据逐字节不变
   （验收标准第 1 条锚点：行为与现状一致）。
"""

from __future__ import annotations

import uuid

from map_types import PlanRevise
from map_types.enums import (
    AgentRole,
    ExperimentPhase,
    ReviewSubstituteKind,
)
from sqlalchemy.orm import Session

from server.domain.models import (
    Agent,
    Experiment,
    PlanVersion,
    Project,
    Review,
)
from server.services import feature_flag_service as svc
from server.services.plan_service import revise_plan
from tests._frontmatter import make_valid_plan

_STUB = "<!-- slim create: plan content lives in map/experiments/rev-dedup/plan.md -->"


def _make_project(db: Session, *, workspace_path: str) -> Project:
    project = Project(
        id=uuid.uuid4(),
        project_key=f"revdedup-{uuid.uuid4().hex[:6]}",
        name="Rev Dedup",
        workspace_path=workspace_path,
    )
    db.add(project)
    db.flush()
    return project


def _make_host(db: Session, *, project_id: uuid.UUID) -> Agent:
    """flag 权限按 persona 解析（name 须 -host 结尾）；名字全局唯一。"""
    agent = Agent(
        id=uuid.uuid4(),
        project_id=project_id,
        name=f"revdedup-{uuid.uuid4().hex[:8]}-host",
        api_token_hash="x",
        api_token_prefix="x",
        role=AgentRole.agent,
    )
    db.add(agent)
    db.flush()
    return agent


def _make_experiment(
    db: Session,
    *,
    project_id: uuid.UUID,
    creator_id: uuid.UUID,
    plan_file_path: str | None,
) -> Experiment:
    exp = Experiment(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=creator_id,
        title="revise dedup fs hash test",
        phase=ExperimentPhase.review,
        current_plan_version=1,
        plan_file_path=plan_file_path,
    )
    db.add(exp)
    db.flush()
    return exp


def _make_plan_v1(db: Session, *, experiment_id, author_id, content: str) -> PlanVersion:
    pv = PlanVersion(
        id=uuid.uuid4(),
        experiment_id=experiment_id,
        version=1,
        content_md=content,
        author_agent_id=author_id,
    )
    db.add(pv)
    db.flush()
    return pv


def _make_review(db: Session, *, experiment_id, reviewer_id, plan_version: int) -> Review:
    review = Review(
        id=uuid.uuid4(),
        experiment_id=experiment_id,
        reviewer_agent_id=reviewer_id,
        plan_version=plan_version,
        substitute_kind=ReviewSubstituteKind.none,
    )
    db.add(review)
    db.flush()
    return review


def _set_plan_retired_flag(db: Session, *, project_id, actor, value: str) -> None:
    svc.set_flag(
        db,
        project_id=project_id,
        flag_key=svc.FLAG_PLAN_DB_CONTENT_RETIRED,
        flag_value=value,
        actor=actor,
        reason="A1-4 dedup gate 测试" if value == "on" else None,
        commit=False,
    )
    db.flush()


def _write_fs_plan(tmp_path, project: Project, slug: str, content: str) -> None:
    plan_dir = tmp_path / "map" / "experiments" / slug
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.md").write_text(content, encoding="utf-8")


def test_flag_on_duplicate_against_fs_plan_no_bump_no_archive(db_session, tmp_path):
    """evidence: flag_on_duplicate_revise_no_bump_no_archive。

    DB v1 是 stub（slim/迁移后形态），提交正文 = FS plan.md 字节 →
    同一版本返回，v1 review 不被归档。旧等值判据下此场景必误 bump。
    """
    project = _make_project(db_session, workspace_path=str(tmp_path))
    host = _make_host(db_session, project_id=project.id)
    exp = _make_experiment(
        db_session,
        project_id=project.id,
        creator_id=host.id,
        plan_file_path="map/experiments/rev-dedup/plan.md",
    )
    v1 = _make_plan_v1(db_session, experiment_id=exp.id, author_id=host.id, content=_STUB)
    reviewer = _make_host(db_session, project_id=project.id)
    review = _make_review(
        db_session, experiment_id=exp.id, reviewer_id=reviewer.id, plan_version=1
    )
    _set_plan_retired_flag(db_session, project_id=project.id, actor=host, value="on")

    content = make_valid_plan(body="## 目标\n同一正文重复提交")
    _write_fs_plan(tmp_path, project, "rev-dedup", content)

    returned = revise_plan(
        db_session,
        exp.id,
        host,
        payload=PlanRevise(content_md=content, change_note="重复提交"),
    )

    assert returned.id == v1.id, "同一 FS plan.md 的重复 revise 不得 bump"
    db_session.refresh(exp)
    assert exp.current_plan_version == 1
    db_session.refresh(review)
    assert review.archived_at is None, "误归档上一版评审 = B 点回归"


def test_flag_on_changed_content_still_bumps_and_archives(db_session, tmp_path):
    """去重不过度抑制：FS plan.md 与提交正文不同 → 正常 bump + 归档级联。"""
    project = _make_project(db_session, workspace_path=str(tmp_path))
    host = _make_host(db_session, project_id=project.id)
    exp = _make_experiment(
        db_session,
        project_id=project.id,
        creator_id=host.id,
        plan_file_path="map/experiments/rev-dedup/plan.md",
    )
    _make_plan_v1(db_session, experiment_id=exp.id, author_id=host.id, content=_STUB)
    reviewer = _make_host(db_session, project_id=project.id)
    review = _make_review(
        db_session, experiment_id=exp.id, reviewer_id=reviewer.id, plan_version=1
    )
    _set_plan_retired_flag(db_session, project_id=project.id, actor=host, value="on")
    _write_fs_plan(
        tmp_path, project, "rev-dedup", make_valid_plan(body="## 目标\nFS 上的旧正文")
    )

    returned = revise_plan(
        db_session,
        exp.id,
        host,
        payload=PlanRevise(
            content_md=make_valid_plan(body="## 目标\n真正的新修订"), change_note="v2"
        ),
    )

    assert returned.version == 2
    db_session.refresh(review)
    assert review.archived_at is not None, "真实修订的归档级联必须照常"


def test_flag_on_missing_fs_plan_falls_back_conservatively(db_session, tmp_path):
    """FS plan.md 缺失（跨机部署）→ 无法判定回退旧等值：stub ≠ 全文 →
    保守 bump（宁可多 bump，不可吞真实修订）。"""
    project = _make_project(db_session, workspace_path=str(tmp_path))
    host = _make_host(db_session, project_id=project.id)
    exp = _make_experiment(
        db_session,
        project_id=project.id,
        creator_id=host.id,
        plan_file_path="map/experiments/rev-dedup/plan.md",
    )
    _make_plan_v1(db_session, experiment_id=exp.id, author_id=host.id, content=_STUB)
    _set_plan_retired_flag(db_session, project_id=project.id, actor=host, value="on")
    # 故意不写 FS plan.md：workspace 不可达场景。

    returned = revise_plan(
        db_session,
        exp.id,
        host,
        payload=PlanRevise(content_md=make_valid_plan(body="新修订"), change_note="v2"),
    )
    assert returned.version == 2


def test_flag_off_equalities_unchanged(db_session, tmp_path):
    """flag off（默认未 set）：等值判据逐字节不变——同 DB 全文 → early-return；
    FS plan.md 存在但与 stub 一致也不影响 flag-off 等值路径。"""
    project = _make_project(db_session, workspace_path=str(tmp_path))
    host = _make_host(db_session, project_id=project.id)
    exp = _make_experiment(
        db_session,
        project_id=project.id,
        creator_id=host.id,
        plan_file_path="map/experiments/rev-dedup/plan.md",
    )
    content = make_valid_plan(body="## 目标\n全文一致")
    v1 = _make_plan_v1(db_session, experiment_id=exp.id, author_id=host.id, content=content)

    returned = revise_plan(
        db_session,
        exp.id,
        host,
        payload=PlanRevise(content_md=content, change_note="重复"),
    )
    assert returned.id == v1.id
    db_session.refresh(exp)
    assert exp.current_plan_version == 1
