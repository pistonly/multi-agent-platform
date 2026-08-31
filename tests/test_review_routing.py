"""T8 pending_review 路由死锁修复回归测试 (实验 37bfd973 I2)。

6 case 直接单元测试 ``prior_version_reviews_fully_resolved_by_experiment``，
覆盖 plan §验收 (a)(b)(f)(g)(h)(i) 路由主路径 + 兼容性护栏：

- (a) v1 resolved → revise v2 → 实验进入 pending_reviews 队列（I1 修复目标）
- (b) v1 未 resolved → 维持排除（与 bd9b21f6 A7 兼容）
- (c)(d)(e)(j) 是 meta-check（全量 pytest / ruff / git diff / 通知路由）
  → 在全量测试 + 提交验证 + wake signal 中体现，不在本单测文件落地

兼容性护栏：
- (f) 多 reviewer 隔离：reviewer_A v1 resolved + revise v2 → reviewer_B
  也能看到该实验（与 reviewer_A 是否接收通知无关）
- (g) v3+ 多次修订不重复入队：v1+v2 resolved + revise v3 → 仍按
  当前 plan_version 单条入队
- (h) 跨实验隔离：experiment_A v1 resolved 不影响 experiment_B v1 pending 判定
- (i) archive 实验场景：archived 实验仍按 pending_review 路由规则判定
  （archive carve-out 是另一条独立 carve-out，T8 不动）

parity 验证：
- pre-fix (无 I1)：batch 函数只看 prior-version resolved，
  v1 resolved + v2 no review → 返回 True (exclude) → 测试 (a) fail
- post-fix (有 I1)：增加 current-version review 存在性检查，
  v1 resolved + v2 no review → 返回 False (include) → 测试 (a) pass
"""
from __future__ import annotations

import uuid

from map_types.enums import (
    ExperimentPhase,
    ResolutionReason,
    ReviewItemKind,
    ReviewItemStatus,
)

from server.domain.models import (
    Agent,
    Experiment,
    Project,
    Review,
    ReviewItem,
)
from server.services.auth import create_agent
from server.services.review_service import (
    prior_version_reviews_fully_resolved_by_experiment,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _project(db_session) -> Project:
    project = Project(
        project_key=f"review-routing-{uuid.uuid4().hex[:6]}",
        name="Review Routing Test",
        workspace_path="/tmp/review-routing",
    )
    db_session.add(project)
    db_session.flush()
    return project


def _agent(db_session, project: Project, name: str) -> Agent:
    agent, _token = create_agent(db_session, name, project_id=project.id)
    return agent


def _experiment(
    db_session,
    project: Project,
    creator: Agent,
    *,
    current_plan_version: int = 2,
    archived: bool = False,
) -> Experiment:
    exp = Experiment(
        project_id=project.id,
        creator_agent_id=creator.id,
        title=f"exp-{uuid.uuid4().hex[:6]}",
        phase=ExperimentPhase.pending_review,
        current_plan_version=current_plan_version,
        archived_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        if archived
        else None,
    )
    db_session.add(exp)
    db_session.flush()
    return exp


def _review(
    db_session,
    experiment: Experiment,
    reviewer: Agent,
    *,
    plan_version: int,
) -> Review:
    review = Review(
        experiment_id=experiment.id,
        reviewer_agent_id=reviewer.id,
        plan_version=plan_version,
    )
    db_session.add(review)
    db_session.flush()
    return review


def _review_item(
    db_session,
    review: Review,
    *,
    kind: ReviewItemKind = ReviewItemKind.unreasonable,
    status: ReviewItemStatus | None = ReviewItemStatus.closed,
    resolution_reason: ResolutionReason | None = ResolutionReason.resolved,
) -> ReviewItem:
    item = ReviewItem(
        review_id=review.id,
        kind=kind,
        content=f"item-{uuid.uuid4().hex[:6]}",
        status=status,
        last_resolution_reason=resolution_reason,
    )
    db_session.add(item)
    db_session.flush()
    return item


# ---------------------------------------------------------------------------
# (a) v1 resolved → revise v2 → pending_reviews 重现（I1 修复目标）
# ---------------------------------------------------------------------------


def test_a_v1_resolved_revise_v2_appears_in_pending_reviews(db_session) -> None:
    """v1 评审不合理项已全部 resolved + revise v2 → 实验进入 pending_reviews。

    parity：
    - pre-fix：batch 返回 True（v1 resolved → 排除）→ 失败
    - post-fix：batch 返回 False（v2 无 review → 保留入队）→ 通过
    """
    project = _project(db_session)
    host = _agent(db_session, project, "host")
    reviewer = _agent(db_session, project, "reviewer")
    exp = _experiment(db_session, project, host, current_plan_version=2)

    v1_review = _review(db_session, exp, reviewer, plan_version=1)
    _review_item(db_session, v1_review)  # closed/resolved 不合理项
    # 当前 v2 无任何 review

    result = prior_version_reviews_fully_resolved_by_experiment(db_session, [exp])
    # carve-out 不应满足（v2 无 review）→ 实验保留在 pending_reviews 队列
    assert result[exp.id] is False, (
        "T8 修复目标：v1 resolved + revise v2 后实验必须出现在 pending_reviews 队列；"
        "若返回 True 表示 carve-out 仍按 v1 误排除，路由死锁未修复"
    )


# ---------------------------------------------------------------------------
# (b) v1 未 resolved → 维持排除（与 bd9b21f6 A7 兼容）
# ---------------------------------------------------------------------------


def test_b_v1_unresolved_keeps_excluded(db_session) -> None:
    """v1 评审不合理项仍有 open → revise v2 后维持排除（不进入队列）。

    这是 bd9b21f6 A7 自动迁回机制的对偶护栏：v1 unresolved 时，
    carve-out 不应满足，reviewer 必须重评 v2。
    """
    project = _project(db_session)
    host = _agent(db_session, project, "host")
    reviewer = _agent(db_session, project, "reviewer")
    exp = _experiment(db_session, project, host, current_plan_version=2)

    v1_review = _review(db_session, exp, reviewer, plan_version=1)
    _review_item(
        db_session,
        v1_review,
        status=ReviewItemStatus.open,  # 未 resolved
        resolution_reason=None,
    )
    # v2 无 review

    result = prior_version_reviews_fully_resolved_by_experiment(db_session, [exp])
    # v1 有 open 项 → carve-out 不满足 → 实验进入 pending_reviews（等待重评）
    assert result[exp.id] is False


# ---------------------------------------------------------------------------
# (f) 多 reviewer 隔离：reviewer_A v1 resolved + revise v2 → reviewer_B 也能看到
# ---------------------------------------------------------------------------


def test_f_multi_reviewer_isolation(db_session) -> None:
    """多 reviewer 隔离：v2 由不同 reviewer 评审也满足 carve-out。

    验证：carve-out 判定只看 "current plan_version 是否有任何 review
    记录"，不限定 reviewer 身份。v1 由 reviewer_A resolved + v2 由
    reviewer_B 评审 → carve-out 满足（排除），与 reviewer_A 是否接收
    通知无关。这避免了 "reviewer_A 已不接收通知，v2 由 reviewer_B
    评审后还把实验留在 reviewer_A 队列" 的反向误判。
    """
    project = _project(db_session)
    host = _agent(db_session, project, "host")
    reviewer_a = _agent(db_session, project, "reviewer-a")
    reviewer_b = _agent(db_session, project, "reviewer-b")
    exp = _experiment(db_session, project, host, current_plan_version=2)

    # v1 by reviewer_A resolved
    v1_review = _review(db_session, exp, reviewer_a, plan_version=1)
    _review_item(db_session, v1_review)
    # v2 by reviewer_B (different reviewer)
    _review(db_session, exp, reviewer_b, plan_version=2)

    result = prior_version_reviews_fully_resolved_by_experiment(db_session, [exp])
    # v2 有任何 review 记录 → carve-out 满足 → 排除
    assert result[exp.id] is True

    # 反向验证：仅 v1 resolved (无 v2 review) → carve-out 不满足 → 入队
    exp_b = _experiment(db_session, project, host, current_plan_version=2)
    b_v1 = _review(db_session, exp_b, reviewer_a, plan_version=1)
    _review_item(db_session, b_v1)

    result_b = prior_version_reviews_fully_resolved_by_experiment(db_session, [exp_b])
    assert result_b[exp_b.id] is False


# ---------------------------------------------------------------------------
# (g) v3+ 多次修订不重复入队
# ---------------------------------------------------------------------------


def test_g_v3_repeated_revise_single_queue_entry(db_session) -> None:
    """v1 + v2 都 resolved + revise v3 → 按当前 plan_version 单条入队。

    函数返回 per-experiment bool，所以"不重复入队"语义就是：
    单个实验在同一 plan_version 下只占一个队列位置。验证：v3 无 review
    → carve-out 不满足 → 实验入队（一次）。
    """
    project = _project(db_session)
    host = _agent(db_session, project, "host")
    reviewer = _agent(db_session, project, "reviewer")
    exp = _experiment(db_session, project, host, current_plan_version=3)

    # v1 + v2 都已 resolved
    for v in (1, 2):
        review = _review(db_session, exp, reviewer, plan_version=v)
        _review_item(db_session, review)
    # v3 无 review

    result = prior_version_reviews_fully_resolved_by_experiment(db_session, [exp])
    # v3 无 review → carve-out 不满足 → 实验入队
    assert result[exp.id] is False


# ---------------------------------------------------------------------------
# (h) 跨实验隔离
# ---------------------------------------------------------------------------


def test_h_cross_experiment_isolation(db_session) -> None:
    """experiment_A v1 resolved 不影响 experiment_B v1 pending 判定。

    A：v1 resolved + v2 无 review → 入队
    B：v1 pending + v2 无 review → 入队
    C：v1 resolved + v2 有 review → 排除

    三个实验的 carve-out 判定互不影响。
    """
    project = _project(db_session)
    host = _agent(db_session, project, "host")
    reviewer = _agent(db_session, project, "reviewer")

    # A：v1 resolved + v2 no review → 入队
    exp_a = _experiment(db_session, project, host, current_plan_version=2)
    a_v1 = _review(db_session, exp_a, reviewer, plan_version=1)
    _review_item(db_session, a_v1)

    # B：v1 pending + v2 no review → 入队
    exp_b = _experiment(db_session, project, host, current_plan_version=2)
    b_v1 = _review(db_session, exp_b, reviewer, plan_version=1)
    _review_item(
        db_session,
        b_v1,
        status=ReviewItemStatus.open,
        resolution_reason=None,
    )

    # C：v1 resolved + v2 has review → 排除
    exp_c = _experiment(db_session, project, host, current_plan_version=2)
    c_v1 = _review(db_session, exp_c, reviewer, plan_version=1)
    _review_item(db_session, c_v1)
    _review(db_session, exp_c, reviewer, plan_version=2)

    result = prior_version_reviews_fully_resolved_by_experiment(
        db_session, [exp_a, exp_b, exp_c]
    )
    assert result[exp_a.id] is False, "A: v1 resolved + v2 no review → 入队"
    assert result[exp_b.id] is False, "B: v1 pending + v2 no review → 入队"
    assert result[exp_c.id] is True, "C: v1 resolved + v2 has review → 排除"


# ---------------------------------------------------------------------------
# (i) archive 实验场景
# ---------------------------------------------------------------------------


def test_i_archived_experiment_unaffected(db_session) -> None:
    """archived 实验的 carve-out 判定与未 archive 实验一致。

    T8 不动 archive carve-out（plan §A10 防御哲学观察）。验证：archived
    实验 v1 resolved + v2 无 review → 仍按 carve-out 规则判定（入队）。
    archive 排除由 todo_service 另一条 carve-out 处理（filter archived_at
    IS NULL），与本函数正交。
    """
    project = _project(db_session)
    host = _agent(db_session, project, "host")
    reviewer = _agent(db_session, project, "reviewer")
    exp = _experiment(db_session, project, host, current_plan_version=2, archived=True)

    v1_review = _review(db_session, exp, reviewer, plan_version=1)
    _review_item(db_session, v1_review)
    # v2 无 review

    result = prior_version_reviews_fully_resolved_by_experiment(db_session, [exp])
    # T8 仅改 carve-out 判定；archive 排除由调用方负责
    assert result[exp.id] is False
