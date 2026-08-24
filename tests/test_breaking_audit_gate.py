"""breaking revise 门禁（实验 bd9b21f6 / plan-revision-review-gate）。

Pins acceptance:

- A1: ``--breaking-audit`` revise 把 running 实验打回 ``pending_review``
- A2: pending_review 期间 ``complete`` 被拒且报错 actionable（含待重评提示）
- A3: 非 breaking revise 不动 phase（running 保持 running）
- A4: breaking revise 缺/过短 change_note 在入口拒绝（单一处置，无警告分支）
- A7: 重评解除三分支——无 open unreasonable 新评审 → 自动迁回 running；
      仍含 unreasonable → 保持 pending_review（complete 持续被拒）
"""

from __future__ import annotations

import uuid

import pytest
from map_types import PlanRevise, ReviewCreate
from map_types.enums import AgentRole, ExperimentPhase
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.domain.models import Agent, Experiment, ExperimentLog, Project
from server.domain.schemas import ExperimentComplete
from server.services.errors import StateTransitionError
from server.services.phase_service import complete_experiment
from server.services.plan_service import revise_plan
from server.services.review_service import create_review
from tests._frontmatter import make_valid_plan


def _setup(db_session: Session) -> tuple[Project, Agent, Agent, Experiment]:
    project = Project(
        id=uuid.uuid4(), project_key="breaking-gate", name="P", workspace_path="/tmp/t"
    )
    db_session.add(project)
    db_session.flush()
    creator = Agent(
        id=uuid.uuid4(),
        project_id=project.id,
        name="creator-host",
        api_token_hash="x",
        api_token_prefix="x",
        role=AgentRole.agent,
    )
    reviewer = Agent(
        id=uuid.uuid4(),
        project_id=project.id,
        name="reviewer-agent",
        api_token_hash="x",
        api_token_prefix="x",
        role=AgentRole.agent,
    )
    db_session.add_all([creator, reviewer])
    db_session.flush()
    exp = Experiment(
        id=uuid.uuid4(),
        project_id=project.id,
        creator_agent_id=creator.id,
        title="breaking audit gate test",
        phase=ExperimentPhase.running,
        current_plan_version=1,
    )
    db_session.add(exp)
    db_session.flush()
    return project, creator, reviewer, exp


def _revise_payload(**kwargs) -> PlanRevise:
    defaults = dict(
        content_md=make_valid_plan(),
        change_note="架构翻转：A1 语义重定义，验收对象改变（为什么：对照物失真）",
        addressed_item_ids=[],
        breaking_audit=True,
    )
    defaults.update(kwargs)
    return PlanRevise(**defaults)


def test_breaking_revise_moves_running_to_pending_review(db_session: Session) -> None:
    _, creator, _, exp = _setup(db_session)
    plan = revise_plan(db_session, exp.id, creator, _revise_payload())
    assert plan.version == 2
    assert exp.phase == ExperimentPhase.pending_review


def test_breaking_revise_without_change_note_refused(db_session: Session) -> None:
    _, creator, _, exp = _setup(db_session)
    with pytest.raises(StateTransitionError, match="change_note"):
        revise_plan(db_session, exp.id, creator, _revise_payload(change_note="短"))
    assert exp.phase == ExperimentPhase.running  # 拒绝后整体不生效


def test_non_breaking_revise_stays_running(db_session: Session) -> None:
    _, creator, _, exp = _setup(db_session)
    revise_plan(db_session, exp.id, creator, _revise_payload(breaking_audit=False))
    assert exp.phase == ExperimentPhase.running


def test_complete_blocked_in_pending_review(db_session: Session) -> None:
    _, creator, _, exp = _setup(db_session)
    revise_plan(db_session, exp.id, creator, _revise_payload())
    assert exp.phase == ExperimentPhase.pending_review
    with pytest.raises(StateTransitionError, match="breaking revise 待重评"):
        complete_experiment(
            db_session, exp.id, creator, ExperimentComplete(summary="x", content_md="log")
        )


def test_re_review_without_unreasonable_releases(db_session: Session) -> None:
    _, creator, reviewer, exp = _setup(db_session)
    revise_plan(db_session, exp.id, creator, _revise_payload())
    create_review(
        db_session,
        exp.id,
        reviewer,
        ReviewCreate(reasonable_items=["v2 语义定稿清晰"], unreasonable_items=[]),
    )
    assert exp.phase == ExperimentPhase.running  # 解除，complete 恢复可用


def test_re_review_with_unreasonable_stays_blocked(db_session: Session) -> None:
    _, creator, reviewer, exp = _setup(db_session)
    revise_plan(db_session, exp.id, creator, _revise_payload())
    create_review(
        db_session,
        exp.id,
        reviewer,
        ReviewCreate(reasonable_items=[], unreasonable_items=["A8 验收缺 evidence"]),
    )
    assert exp.phase == ExperimentPhase.pending_review  # 持续拦截
    with pytest.raises(StateTransitionError, match="open unreasonable items: 1"):
        complete_experiment(
            db_session, exp.id, creator, ExperimentComplete(summary="x", content_md="log")
        )


def _engineered_log_red_flags(db_session: Session, experiment_id: uuid.UUID) -> list[str]:
    return [
        lg.content_md
        for lg in db_session.scalars(
            select(ExperimentLog).where(ExperimentLog.experiment_id == experiment_id)
        ).all()
    ]


def test_a5_red_flag_when_revised_without_reexperience(db_session: Session) -> None:
    """A5 漏标防线正例：评审通过后又有 plan 修订、当前版本无评审覆盖 → complete
    不拦（A3 非 breaking 允许）但完成日志落「breaking 漏标红旗」供 reviewer 核对。"""
    _, creator, reviewer, exp = _setup(db_session)
    exp.phase = ExperimentPhase.review  # 模拟 v1 已评审通过
    db_session.flush()
    create_review(
        db_session,
        exp.id,
        reviewer,
        ReviewCreate(reasonable_items=["v1 可行"], unreasonable_items=[]),
    )
    exp.phase = ExperimentPhase.running
    db_session.flush()
    # 漏标形态：架构级修订却非 breaking 标记 → 保持 running，版本升到 v2
    revise_plan(db_session, exp.id, creator, _revise_payload(breaking_audit=False))
    assert exp.current_plan_version == 2
    assert exp.phase == ExperimentPhase.running
    complete_experiment(
        db_session,
        exp.id,
        creator,
        ExperimentComplete(
            summary="完成",
            content_md="执行完成",
            metadata={"evidence": {"pytest_summary": "1 passed"}},
        ),
    )
    assert any("breaking 漏标红旗" in md for md in _engineered_log_red_flags(db_session, exp.id))


def test_a5_no_red_flag_after_review_covers_current_version(db_session: Session) -> None:
    """A5 反例：breaking 打回 → reviewer 重评覆盖当前版本（A7 解除）→ complete
    无红旗（重评链路完整）。"""
    _, creator, reviewer, exp = _setup(db_session)
    revise_plan(db_session, exp.id, creator, _revise_payload())
    assert exp.phase == ExperimentPhase.pending_review
    create_review(
        db_session,
        exp.id,
        reviewer,
        ReviewCreate(reasonable_items=["v2 定稿清晰"], unreasonable_items=[]),
    )
    assert exp.phase == ExperimentPhase.running
    complete_experiment(
        db_session,
        exp.id,
        creator,
        ExperimentComplete(
            summary="完成",
            content_md="执行完成",
            metadata={"evidence": {"pytest_summary": "1 passed"}},
        ),
    )
    assert not any(
        "breaking 漏标红旗" in md for md in _engineered_log_red_flags(db_session, exp.id)
    )


def test_a6_misfire_breaking_corrected_once_no_penalty(db_session: Session) -> None:
    """A6 误标容忍：非 breaking 内容误打 --breaking-audit → 回 pending_review
    一次，reviewer 重评确认后解除 → complete 正常放行（一次纠正、不惩罚、无红旗）。"""
    _, creator, reviewer, exp = _setup(db_session)
    revise_plan(db_session, exp.id, creator, _revise_payload(breaking_audit=True))
    assert exp.phase == ExperimentPhase.pending_review
    create_review(
        db_session,
        exp.id,
        reviewer,
        ReviewCreate(
            reasonable_items=["确认属笔误级修订，误标 breaking 除外"], unreasonable_items=[]
        ),
    )
    assert exp.phase == ExperimentPhase.running
    complete_experiment(
        db_session,
        exp.id,
        creator,
        ExperimentComplete(
            summary="完成",
            content_md="执行完成",
            metadata={"evidence": {"pytest_summary": "1 passed"}},
        ),
    )
    assert not any(
        "breaking 漏标红旗" in md for md in _engineered_log_red_flags(db_session, exp.id)
    )
