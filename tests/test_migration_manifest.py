"""Migration manifest service 测试（实验 M2 I5：A5）。

覆盖：

- scan_project 幂等性：重跑不会产生重复 item 行（UNIQUE 索引 + INSERT OR IGNORE）
- scan_project 输出字段：by_kind / by_status 正确
- reset_stale_in_flight：超过 STALE_AFTER 的 in_flight 翻回 pending；
  新的 in_flight 不动；非 in_flight 不动
- claim / mark_applied / mark_failed：原子状态机；attempts 计数；
  超 MAX_ATTEMPTS 才进 failed 终态
- 重启 scan 后旧 run 的 applied 项仍存在，新 run 跳过（idempotency）
- 中断恢复链路：scan → claim → 假装崩 → reset_stale → claim 又拿回 → mark_applied
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from map_types.enums import (
    ExperimentMode,
    ExperimentPhase,
    TopicStatus,
)
from sqlalchemy import select

from server.domain.models import (
    Agent,
    Experiment,
    MigrationManifestItem,
    MigrationRun,
    Topic,
)
from server.services import migration_manifest_service as svc


def _make_agent(db_session, *, project_id, name="manifest-agent"):
    """最小可用 Agent；name 不带 persona 后缀，与 service gate 无关。"""
    from map_types.enums import AgentRole

    agent = Agent(
        id=uuid.uuid4(),
        name=name,
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
    from server.domain.models import Project

    project = Project(
        id=uuid.uuid4(),
        project_key=f"mm-{uuid.uuid4().hex[:8]}",
        name="manifest test",
        workspace_path="/tmp/mm-test",
        content_root="map",
    )
    db_session.add(project)
    db_session.flush()
    return project


def _make_topic(db_session, *, project_id, slug, creator_agent_id):
    topic = Topic(
        id=uuid.uuid4(),
        slug=slug,
        title=f"topic-{slug}",
        status=TopicStatus.open,
        discussion_round="intake",
        project_id=project_id,
        creator_agent_id=creator_agent_id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(topic)
    db_session.flush()
    return topic


def _make_experiment(db_session, *, project_id, slug, creator_agent_id):
    experiment = Experiment(
        id=uuid.uuid4(),
        title=f"exp-{slug}",
        description="manifest test experiment",
        phase=ExperimentPhase.draft,
        mode=ExperimentMode.standard,
        project_id=project_id,
        creator_agent_id=creator_agent_id,
        current_plan_version=1,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(experiment)
    db_session.flush()
    return experiment


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------


def test_scan_project_creates_pending_items(db_session):
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="t1", creator_agent_id=agent.id)
    _make_topic(db_session, project_id=project.id, slug="t2", creator_agent_id=agent.id)
    _make_experiment(
        db_session, project_id=project.id, slug="e1", creator_agent_id=agent.id
    )

    report = svc.scan_project(db_session, project_id=project.id)

    assert report.scanned == 3
    assert report.inserted == 3
    assert report.skipped_existing == 0
    assert report.by_kind == {"topic": 2, "experiment": 1}
    items = db_session.scalars(
        select(MigrationManifestItem).where(MigrationManifestItem.run_id == report.run_id)
    ).all()
    assert len(items) == 3
    assert {i.status for i in items} == {"pending"}
    assert all(i.attempts == 0 for i in items)
    # idempotency_key 不重复（unique 索引自然保证，但显式断言）
    keys = [i.idempotency_key for i in items]
    assert len(set(keys)) == 3


def test_scan_project_idempotent_on_rerun(db_session):
    """重跑 scan 不会产生重复 item 行 —— UNIQUE 索引 + INSERT OR IGNORE。"""
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="t1", creator_agent_id=agent.id)
    _make_experiment(
        db_session, project_id=project.id, slug="e1", creator_agent_id=agent.id
    )

    first = svc.scan_project(db_session, project_id=project.id)
    second = svc.scan_project(db_session, project_id=project.id)

    # 第二次跑应该被 unique 约束全跳过
    assert first.scanned == 2 and first.inserted == 2
    assert second.scanned == 2 and second.inserted == 0
    assert second.skipped_existing == 2

    # DB 里总共还是 2 行（不同 run_id，但 idempotency_key 唯一）
    all_items = db_session.scalars(
        select(MigrationManifestItem).where(
            MigrationManifestItem.project_id == project.id
        )
    ).all()
    assert len(all_items) == 2


def test_scan_project_same_slug_different_hash_creates_separate_items(db_session):
    """同 slug 内容变了 → 不同 content_hash → 不同 item 行。

    重要：这样旧 run 的 applied item 不会被新内容覆盖；新内容生成独立
    item 行待 apply。
    """
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    topic = _make_topic(
        db_session, project_id=project.id, slug="mutating", creator_agent_id=agent.id
    )

    first = svc.scan_project(db_session, project_id=project.id)
    # 标记第一次 apply 完成（模拟 host 端已经把 topic 同步到 FS）
    item_first = db_session.scalar(
        select(MigrationManifestItem).where(MigrationManifestItem.run_id == first.run_id)
    )
    svc.mark_applied(db_session, item_id=item_first.id)

    # 修改 title → content_hash 变了 → 新 scan 会建独立 item 行
    topic.title = "mutating (revised)"
    db_session.flush()

    second = svc.scan_project(db_session, project_id=project.id)
    assert second.inserted == 1  # 新内容，新行
    assert second.skipped_existing == 0

    # 两个 item 行共存：旧 applied + 新 pending
    items = db_session.scalars(
        select(MigrationManifestItem).where(
            MigrationManifestItem.project_id == project.id
        ).order_by(MigrationManifestItem.id)
    ).all()
    assert len(items) == 2
    statuses = sorted([i.status for i in items])
    assert statuses == ["applied", "pending"]


# ---------------------------------------------------------------------------
# state machine: claim / mark_applied / mark_failed
# ---------------------------------------------------------------------------


def test_claim_atomic_pending_to_in_flight(db_session):
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="t1", creator_agent_id=agent.id)
    report = svc.scan_project(db_session, project_id=project.id)
    item = db_session.scalar(
        select(MigrationManifestItem).where(MigrationManifestItem.run_id == report.run_id)
    )

    claimed = svc.claim(db_session, item_id=item.id)

    assert claimed is not None
    assert claimed.status == "in_flight"
    assert claimed.attempts == 1
    assert claimed.last_attempt_at is not None


def test_claim_returns_none_when_already_in_flight(db_session):
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="t1", creator_agent_id=agent.id)
    report = svc.scan_project(db_session, project_id=project.id)
    item = db_session.scalar(
        select(MigrationManifestItem).where(MigrationManifestItem.run_id == report.run_id)
    )

    svc.claim(db_session, item_id=item.id)
    again = svc.claim(db_session, item_id=item.id)

    assert again is None


def test_mark_applied_clears_error_and_sets_timestamp(db_session):
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="t1", creator_agent_id=agent.id)
    report = svc.scan_project(db_session, project_id=project.id)
    item = db_session.scalar(
        select(MigrationManifestItem).where(MigrationManifestItem.run_id == report.run_id)
    )
    svc.claim(db_session, item_id=item.id)

    svc.mark_applied(db_session, item_id=item.id)

    db_session.refresh(item)
    assert item.status == "applied"
    assert item.applied_at is not None
    assert item.last_error is None


def test_mark_failed_under_budget_returns_to_pending(db_session):
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="t1", creator_agent_id=agent.id)
    report = svc.scan_project(db_session, project_id=project.id)
    item = db_session.scalar(
        select(MigrationManifestItem).where(MigrationManifestItem.run_id == report.run_id)
    )
    svc.claim(db_session, item_id=item.id)  # attempts = 1

    result = svc.mark_failed(db_session, item_id=item.id, error="boom")

    assert result.status == "pending"  # < MAX_ATTEMPTS, retry
    assert result.last_error == "boom"
    assert result.attempts == 1


def test_mark_failed_at_max_attempts_terminal_failed(db_session):
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="t1", creator_agent_id=agent.id)
    report = svc.scan_project(db_session, project_id=project.id)
    item = db_session.scalar(
        select(MigrationManifestItem).where(MigrationManifestItem.run_id == report.run_id)
    )

    # 模拟 3 次失败（MAX_ATTEMPTS）
    for _ in range(svc.MAX_ATTEMPTS):
        svc.claim(db_session, item_id=item.id)
        item = svc.mark_failed(db_session, item_id=item.id, error="boom")

    assert item.status == "failed"
    assert item.attempts == svc.MAX_ATTEMPTS


# ---------------------------------------------------------------------------
# interrupt recovery: reset_stale_in_flight
# ---------------------------------------------------------------------------


def test_reset_stale_in_flight_flips_old_to_pending(db_session):
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="stale", creator_agent_id=agent.id)
    report = svc.scan_project(db_session, project_id=project.id)
    item = db_session.scalar(
        select(MigrationManifestItem).where(MigrationManifestItem.run_id == report.run_id)
    )
    svc.claim(db_session, item_id=item.id)

    # 手动把 last_attempt_at 推回 31 分钟前
    item.last_attempt_at = datetime.now(timezone.utc) - timedelta(
        seconds=svc.STALE_AFTER_SECONDS + 60
    )
    db_session.commit()

    reset_count = svc.reset_stale_in_flight(db_session, project_id=project.id)
    assert reset_count == 1

    db_session.refresh(item)
    assert item.status == "pending"
    assert "stale in_flight reset" in (item.last_error or "")


def test_reset_stale_in_flight_skips_fresh_in_flight(db_session):
    """刚 claim 的 item 不会被重置 —— 阈值内 in_flight 不算 stale。"""
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="fresh", creator_agent_id=agent.id)
    report = svc.scan_project(db_session, project_id=project.id)
    item = db_session.scalar(
        select(MigrationManifestItem).where(MigrationManifestItem.run_id == report.run_id)
    )
    svc.claim(db_session, item_id=item.id)

    reset_count = svc.reset_stale_in_flight(db_session, project_id=project.id)
    assert reset_count == 0

    db_session.refresh(item)
    assert item.status == "in_flight"


def test_reset_stale_in_flight_skips_non_in_flight(db_session):
    """applied / failed / pending 都不会被重置 —— 只有卡死的 in_flight。"""
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="t1", creator_agent_id=agent.id)
    report = svc.scan_project(db_session, project_id=project.id)
    item = db_session.scalar(
        select(MigrationManifestItem).where(MigrationManifestItem.run_id == report.run_id)
    )
    svc.mark_applied(db_session, item_id=item.id)

    # 即便伪造 last_attempt_at，applied 状态也不能被翻回 pending
    item.last_attempt_at = datetime.now(timezone.utc) - timedelta(
        seconds=svc.STALE_AFTER_SECONDS + 60
    )
    db_session.commit()

    reset_count = svc.reset_stale_in_flight(db_session, project_id=project.id)
    assert reset_count == 0

    db_session.refresh(item)
    assert item.status == "applied"


# ---------------------------------------------------------------------------
# interrupt recovery end-to-end: kill → resume picks up at last in_flight
# ---------------------------------------------------------------------------


def test_kill_during_execute_resume_picks_up_stale_item(db_session):
    """进程在 claim 后崩掉 → 模拟重启 → reset_stale → claim 又拿回 → mark_applied。

    这是 host 风险提示里点名的中断恢复实测：用 service 层模拟「kill -9」
    效果（fake last_attempt_at + claim 之间的空档），验证 resume 能
    拿回卡死的 item。
    """
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(
        db_session, project_id=project.id, slug="recover-1", creator_agent_id=agent.id
    )
    _make_topic(
        db_session, project_id=project.id, slug="recover-2", creator_agent_id=agent.id
    )
    report = svc.scan_project(db_session, project_id=project.id)
    items = db_session.scalars(
        select(MigrationManifestItem)
        .where(MigrationManifestItem.run_id == report.run_id)
        .order_by(MigrationManifestItem.id)
    ).all()

    # 进程 1: claim 第一项（attempts=1，in_flight），然后「崩」
    svc.claim(db_session, item_id=items[0].id)
    # 把 last_attempt_at 推回 stale 阈值之前
    items[0].last_attempt_at = datetime.now(timezone.utc) - timedelta(
        seconds=svc.STALE_AFTER_SECONDS + 60
    )
    db_session.commit()

    # 第二项已经成功 apply
    svc.claim(db_session, item_id=items[1].id)
    svc.mark_applied(db_session, item_id=items[1].id)

    # 进程 2 (resume): reset stale + list actionable + 重新 claim
    reset = svc.reset_stale_in_flight(db_session, project_id=project.id)
    assert reset == 1  # 第一项被捡回

    actionable = svc.list_actionable(db_session, run_id=report.run_id)
    assert len(actionable) == 1
    assert actionable[0].id == items[0].id
    assert actionable[0].status == "pending"

    # 重新 claim → apply
    reclaimed = svc.claim(db_session, item_id=items[0].id)
    assert reclaimed is not None
    assert reclaimed.attempts == 2  # 第一次 + 第二次 = 2
    svc.mark_applied(db_session, item_id=items[0].id)

    # 最终：两项都 applied
    final = db_session.scalars(
        select(MigrationManifestItem).where(MigrationManifestItem.run_id == report.run_id)
    ).all()
    statuses = sorted([i.status for i in final])
    assert statuses == ["applied", "applied"]


# ---------------------------------------------------------------------------
# idempotency_key helpers
# ---------------------------------------------------------------------------


def test_idempotency_key_differs_by_project_kind_slug_hash():
    """idempotency_key 必须区分 (project_id, kind, slug, content_hash) 全部四元。"""
    p1 = uuid.uuid4()
    p2 = uuid.uuid4()
    k1 = svc._compute_idempotency_key(
        project_id=p1, kind="topic", slug="x", content_hash="h1"
    )
    k2 = svc._compute_idempotency_key(
        project_id=p2, kind="topic", slug="x", content_hash="h1"
    )
    k3 = svc._compute_idempotency_key(
        project_id=p1, kind="experiment", slug="x", content_hash="h1"
    )
    k4 = svc._compute_idempotency_key(
        project_id=p1, kind="topic", slug="y", content_hash="h1"
    )
    k5 = svc._compute_idempotency_key(
        project_id=p1, kind="topic", slug="x", content_hash="h2"
    )
    # 全部 4 元任一不同 → key 不同
    assert len({k1, k2, k3, k4, k5}) == 5
    # 相同 4 元 → key 相同（确定性）
    k1_again = svc._compute_idempotency_key(
        project_id=p1, kind="topic", slug="x", content_hash="h1"
    )
    assert k1_again == k1


def test_summarize_run_counts_status_distribution(db_session):
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    for i in range(3):
        _make_topic(
            db_session,
            project_id=project.id,
            slug=f"t{i}",
            creator_agent_id=agent.id,
        )
    report = svc.scan_project(db_session, project_id=project.id)
    items = db_session.scalars(
        select(MigrationManifestItem).where(MigrationManifestItem.run_id == report.run_id)
    ).all()
    svc.claim(db_session, item_id=items[0].id)
    svc.mark_applied(db_session, item_id=items[0].id)
    svc.claim(db_session, item_id=items[1].id)  # stays in_flight

    summary = svc.summarize_run(db_session, run_id=report.run_id)

    assert summary["applied"] == 1
    assert summary["in_flight"] == 1
    assert summary["pending"] == 1
    assert summary["failed"] == 0


def test_finish_run_records_summary_and_timestamp(db_session):
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(
        db_session, project_id=project.id, slug="t1", creator_agent_id=agent.id
    )
    report = svc.scan_project(db_session, project_id=project.id)

    svc.finish_run(
        db_session,
        run_id=report.run_id,
        summary={"applied": 1, "pending": 0, "failed": 0},
    )

    run = db_session.get(MigrationRun, report.run_id)
    assert run.finished_at is not None
    assert "applied" in (run.summary or "")


# ---------------------------------------------------------------------------
# M3 A3：canonical JSON content_hash
# ---------------------------------------------------------------------------


def test_content_hash_canonical_and_order_insensitive():
    """同 payload 不同键序 → 同 hash；口径 = canonical JSON SHA-256。

    历史（M2 I5）hash 用 ``repr((kind, payload))``：依赖 dict 插入序，
    与 FS 端 canonical 口径永不互认。M3 A3 改 canonical JSON 后，
    键序不再影响结果，且可独立复算验证。
    """
    import hashlib
    import json

    payload_a = {"title": "t", "phase": "draft", "slug": "s"}
    payload_b = {"slug": "s", "phase": "draft", "title": "t"}
    hash_a = svc._compute_content_hash("experiment", payload_a)
    hash_b = svc._compute_content_hash("experiment", payload_b)
    assert hash_a == hash_b

    # 独立复算：canonical JSON（sort_keys + 紧凑分隔符 + UTF-8）+ kind 隔离
    expected = hashlib.sha256(
        json.dumps(
            {"kind": "experiment", "payload": payload_a},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    assert hash_a == expected

    # kind 参与摘要：topic vs experiment 同 payload 不碰撞
    assert svc._compute_content_hash("topic", payload_a) != hash_a


# ---------------------------------------------------------------------------
# M3 A2：project 范围 actionable / plan 只读
# ---------------------------------------------------------------------------


def test_list_project_actionable_includes_skipped_and_cross_run(db_session):
    """project 级 actionable = 全 run 的 pending + skipped（dry 排练标记可捡回）。"""
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="t1", creator_agent_id=agent.id)
    report1 = svc.scan_project(db_session, project_id=project.id)
    item1 = db_session.scalar(
        select(MigrationManifestItem).where(MigrationManifestItem.run_id == report1.run_id)
    )
    svc.mark_applied(db_session, item_id=item1.id)

    # 第二轮 scan（idempotent 跳过 applied 的 t1）+ 新 topic 产生新 pending
    _make_topic(db_session, project_id=project.id, slug="t2", creator_agent_id=agent.id)
    _make_topic(db_session, project_id=project.id, slug="t3", creator_agent_id=agent.id)
    svc.scan_project(db_session, project_id=project.id)

    pending2 = db_session.scalars(
        select(MigrationManifestItem).where(
            MigrationManifestItem.project_id == project.id,
            MigrationManifestItem.status == "pending",
        )
    ).all()
    assert len(pending2) == 2  # t2 + t3（t1 同 hash 幂等跳过，不产生新行）
    # 把其中一个翻成 skipped（dry execute 的排练标记）
    svc._mark_skipped(db_session, item_id=pending2[0].id)

    actionable = svc.list_project_actionable(db_session, project_id=project.id, limit=50)
    statuses = sorted(i.status for i in actionable)
    assert statuses == ["pending", "skipped"]  # applied 的不在列


def test_plan_project_is_read_only(db_session):
    """plan 不改任何 item 状态（dry-run 是登记过的只读命令，不能有副作用）。"""
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="t1", creator_agent_id=agent.id)
    svc.scan_project(db_session, project_id=project.id)
    before = {
        i.id: i.status
        for i in db_session.scalars(select(MigrationManifestItem)).all()
    }

    plan = svc.plan_project(db_session, project_id=project.id, limit=50)

    after = {
        i.id: i.status
        for i in db_session.scalars(select(MigrationManifestItem)).all()
    }
    assert plan["actionable_count"] == 1
    assert before == after


# ---------------------------------------------------------------------------
# M3 A2：server 端 execute 循环
# ---------------------------------------------------------------------------


def test_execute_pending_dry_marks_skipped_not_applied(db_session):
    """dry（apply=False）→ skipped（可被 --apply 捡回），不再伪造 applied。"""
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="dry-1", creator_agent_id=agent.id)
    # execute 只处理既有 manifest 行，不隐式 scan
    svc.scan_project(db_session, project_id=project.id)

    report = svc.execute_pending(
        db_session, project=project, agent=agent, apply=False, limit=50
    )

    assert report["apply"] is False
    assert report["processed"] == 1
    assert report["failed"] == 0
    summary = svc.summarize_project(db_session, project_id=project.id)
    assert summary["skipped"] == 1
    assert summary["applied"] == 0
    # skipped 仍在 actionable 里 —— --apply 一轮即可捡回
    actionable = svc.list_project_actionable(db_session, project_id=project.id)
    assert [i.status for i in actionable] == ["skipped"]


def test_execute_pending_apply_failure_tags_stale_code(db_session):
    """apply 失败 → mark_failed_with_stale：last_error 带 stable stale code。

    topic kind 目前无 apply 路径（_apply_item 只支持 experiment），会以
    ``unsupported manifest kind`` 失败 —— 正好用作失败注入点，验证
    stale code 分类与 retry budget 落库。
    """
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="boom", creator_agent_id=agent.id)
    svc.scan_project(db_session, project_id=project.id)

    report = svc.execute_pending(
        db_session, project=project, agent=agent, apply=True, limit=50
    )

    assert report["processed"] == 0
    assert report["failed"] == 1
    item = db_session.scalar(select(MigrationManifestItem))
    # attempts=1 未到 MAX_ATTEMPTS → 回 pending 等重试，但错误留痕已落库
    assert item.attempts == 1
    assert item.status == "pending"
    assert item.last_error is not None
    assert item.last_error.startswith(f"[{svc.STALE_OTHER}]")


def test_push_delta_retries_on_revision_conflict(monkeypatch, db_session):
    """CAS base_revision 冲突 → 刷新投影行重建请求重试；非冲突冲突立即上抛。"""
    from types import SimpleNamespace

    from map_types.schemas.fs import (
        FsExperimentRead,
        FsProjectionChange,
        FsProjectionDeltaRequest,
    )

    from server.services import fs_projection_store as store
    from server.services.errors import ConflictError

    row = SimpleNamespace(revision=7)
    calls = {"apply": 0}

    def fake_get(db, project):
        return row

    def fake_apply(db, project, agent, payload):
        calls["apply"] += 1
        if calls["apply"] == 1:
            # 模拟别的 writer 先推进了 revision
            row.revision = 8
            raise ConflictError(
                "projection revision conflict: expected 8, got 7",
                error="fs_projection_conflict",
            )
        assert payload.base_revision == 8  # 重试请求带刷新后的 base_revision
        return SimpleNamespace(revision=8)

    def fake_build(db, *, project, row, changes):
        return FsProjectionDeltaRequest(
            base_revision=row.revision,
            client_workspace="test",
            content_root="map",
            changes=changes,
            result_content_hash="0" * 64,
        )

    monkeypatch.setattr(store, "get_fs_projection", fake_get)
    monkeypatch.setattr(store, "apply_fs_projection_delta", fake_apply)
    # _build_delta_request 会读投影 payload（真实 FsProjection 行才有）；
    # 本测试只关心 CAS 重试语义，投影内容置空即可
    monkeypatch.setattr(store, "projection_payload_topics", lambda row: [])
    monkeypatch.setattr(
        store, "projection_payload_experiments", lambda row: [exp_value]
    )

    exp_value = FsExperimentRead.model_validate(
        {
            "id": uuid.uuid4(),
            "slug": "s",
            "title": "t",
            "description": "",
            "phase": "draft",
            "creator": "a",
            "dir_path": "map/experiments/s",
        }
    )
    change = FsProjectionChange(kind="experiment_upsert", slug="s", value=exp_value)
    result = svc._push_delta(
        db_session,
        project=SimpleNamespace(workspace_path="/tmp", content_root="map"),
        agent=SimpleNamespace(id=uuid.uuid4()),
        changes=[change],
    )
    assert result.revision == 8
    assert calls["apply"] == 2

    # 非 revision 类冲突（发布者绑定）不重试
    calls["apply"] = 0

    def fake_apply_binding(db, project, agent, payload):
        calls["apply"] += 1
        raise ConflictError(
            "FS projection is bound to another single publisher",
            error="fs_projection_conflict",
        )

    monkeypatch.setattr(store, "apply_fs_projection_delta", fake_apply_binding)
    try:
        svc._push_delta(
            db_session,
            project=SimpleNamespace(workspace_path="/tmp", content_root="map"),
            agent=SimpleNamespace(id=uuid.uuid4()),
            changes=[change],
        )
        raise AssertionError("expected ConflictError")
    except ConflictError as exc:
        assert "bound to another" in str(exc)
    assert calls["apply"] == 1


def test_verify_project_aligns_against_fs_view(db_session, monkeypatch):
    """verify 按 projection_id 关联 DB ↔ server FS 视角，逐字段比对。"""
    from types import SimpleNamespace

    from server.services import fs_source_service as fss

    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    exp_ok = _make_experiment(
        db_session, project_id=project.id, slug="ok", creator_agent_id=agent.id
    )
    exp_drift = _make_experiment(
        db_session, project_id=project.id, slug="drift", creator_agent_id=agent.id
    )
    svc.scan_project(db_session, project_id=project.id)
    # 两个实验的 item 全部置 applied（verify 只看 applied）
    for it in db_session.scalars(select(MigrationManifestItem)).all():
        svc.mark_applied(db_session, item_id=it.id)

    def fake_view(db, project_arg):
        return [
            SimpleNamespace(
                projection_id=exp_ok.id,
                slug="fs-ok",
                title=exp_ok.title,
                phase="draft",
                description=exp_ok.description,
                current_plan_version=1,
                creator="manifest-agent",
                executor="",
            ),
            SimpleNamespace(
                projection_id=exp_drift.id,
                slug="fs-drift",
                title="DRIFTED TITLE",  # 与 DB 权威值不一致
                phase="draft",
                description=exp_drift.description,
                current_plan_version=1,
                creator="manifest-agent",
                executor="",
            ),
        ]

    monkeypatch.setattr(fss, "fs_experiments_view", fake_view)

    report = svc.verify_project(db_session, project=project)

    assert report["verified_count"] == 1
    assert report["mismatch_count"] == 1
    assert report["missing_count"] == 0
    assert report["mismatched"][0]["fields"][0]["field"] == "title"
    assert report["mismatched"][0]["fields"][0]["fs_view"] == "DRIFTED TITLE"
