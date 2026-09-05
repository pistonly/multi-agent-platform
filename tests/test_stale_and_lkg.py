"""Stale semantics 六类 + last-known-good fallback（实验 M2 I6：A6）。

覆盖：

- classify_stale_code 6 类 + STALE_OTHER 兜底
- mark_failed_with_stale 把分类 code 拼到 last_error 前缀
- build_lkg_payload 只取 status=applied 的 item
- diff_against_lkg 四种 bucket：in_both / in_db_only / in_lkg_only / hash_drift
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from map_types.enums import ExperimentMode, ExperimentPhase, TopicStatus
from sqlalchemy import select

from server.domain.models import (
    Agent,
    Experiment,
    MigrationManifestItem,
    Topic,
)
from server.services import migration_manifest_service as svc


def _make_agent(db_session, *, project_id):
    from map_types.enums import AgentRole

    agent = Agent(
        id=uuid.uuid4(),
        name="lkg-agent",
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
        project_key=f"lkg-{uuid.uuid4().hex[:8]}",
        name="lkg test",
        workspace_path="/tmp/lkg-test",
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
        description="lkg test experiment",
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


def _scan_items(db_session, *, run_id):
    return list(
        db_session.scalars(
            select(MigrationManifestItem).where(MigrationManifestItem.run_id == run_id)
        )
    )


# ---------------------------------------------------------------------------
# classify_stale_code
# ---------------------------------------------------------------------------


def test_classify_stale_projection_revision_conflict():
    assert svc.classify_stale_code("projection revision conflict on base_revision=42") == (
        svc.STALE_PROJECTION_REVISION_CONFLICT
    )


def test_classify_stale_content_hash_mismatch():
    assert svc.classify_stale_code({"detail": "content hash mismatch (expected_hash=abc)"}) == (
        svc.STALE_CONTENT_HASH_MISMATCH
    )
    assert svc.classify_stale_code("tombstone rejection: hash mismatch") == (
        svc.STALE_CONTENT_HASH_MISMATCH
    )


def test_classify_stale_publisher_not_allowed():
    assert svc.classify_stale_code("publisher not allowed for project 7490...") == (
        svc.STALE_PUBLISHER_NOT_ALLOWED
    )
    assert svc.classify_stale_code({"detail": "publisher_agent_id not in publisher allowlist"}) == (
        svc.STALE_PUBLISHER_NOT_ALLOWED
    )


def test_classify_stale_payload_too_large():
    assert svc.classify_stale_code("payload too large (max_bytes=8388608)") == (
        svc.STALE_PAYLOAD_TOO_LARGE
    )
    assert svc.classify_stale_code("HTTP 413 payload size exceeds maximum") == (
        svc.STALE_PAYLOAD_TOO_LARGE
    )


def test_classify_stale_write_token_replay():
    assert svc.classify_stale_code("write_token nonce already used") == (
        svc.STALE_WRITE_TOKEN_REPLAY
    )
    assert svc.classify_stale_code("fs_write_receipts unique constraint violated") == (
        svc.STALE_WRITE_TOKEN_REPLAY
    )


def test_classify_stale_schema_mismatch():
    assert svc.classify_stale_code("validation error: field required") == (
        svc.STALE_SCHEMA_MISMATCH
    )
    assert svc.classify_stale_code("HTTP 422 unprocessable entity") == (
        svc.STALE_SCHEMA_MISMATCH
    )


def test_classify_stale_other_fallback():
    """未知错误归 STALE_OTHER；不应抛。"""
    assert svc.classify_stale_code("some random unclassified error") == svc.STALE_OTHER
    assert svc.classify_stale_code(Exception("weird")) == svc.STALE_OTHER
    assert svc.classify_stale_code({"detail": "unknown future error"}) == svc.STALE_OTHER


def test_classify_stale_priority_order_cas_first():
    """revision conflict 关键词优先于 hash mismatch —— 因为冲突的 error
    message 里可能同时含 ``hash`` 字样（CAS 也校验 hash）。"""
    text = "projection revision conflict + content hash mismatch"
    assert svc.classify_stale_code(text) == svc.STALE_PROJECTION_REVISION_CONFLICT


def test_stale_all_codes_constant_covers_six_plus_other():
    """契约：6 类 + STALE_OTHER 共 7 个 code；后续扩 code 加进 STALE_ALL_CODES。"""
    assert len(svc.STALE_ALL_CODES) == 7
    assert svc.STALE_OTHER in svc.STALE_ALL_CODES
    for code in (
        svc.STALE_PROJECTION_REVISION_CONFLICT,
        svc.STALE_CONTENT_HASH_MISMATCH,
        svc.STALE_PUBLISHER_NOT_ALLOWED,
        svc.STALE_PAYLOAD_TOO_LARGE,
        svc.STALE_WRITE_TOKEN_REPLAY,
        svc.STALE_SCHEMA_MISMATCH,
    ):
        assert code in svc.STALE_ALL_CODES


# ---------------------------------------------------------------------------
# mark_failed_with_stale
# ---------------------------------------------------------------------------


def test_mark_failed_with_stale_tags_code_prefix(db_session):
    """``[stale.code] <原 error>`` 前缀写入 ``last_error``。"""
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="t1", creator_agent_id=agent.id)
    report = svc.scan_project(db_session, project_id=project.id)
    item = db_session.scalar(
        select(MigrationManifestItem).where(MigrationManifestItem.run_id == report.run_id)
    )
    svc.claim(db_session, item_id=item.id)

    svc.mark_failed_with_stale(
        db_session,
        item_id=item.id,
        error="detail message",
        exc="projection revision conflict on base_revision=99",
    )

    db_session.refresh(item)
    assert item.last_error.startswith("[stale.projection_revision_conflict] ")
    assert "detail message" in item.last_error


# ---------------------------------------------------------------------------
# build_lkg_payload + diff_against_lkg
# ---------------------------------------------------------------------------


def test_build_lkg_payload_only_includes_applied_items(db_session):
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="applied", creator_agent_id=agent.id)
    _make_topic(db_session, project_id=project.id, slug="failed", creator_agent_id=agent.id)
    _make_experiment(db_session, project_id=project.id, slug="exp", creator_agent_id=agent.id)
    report = svc.scan_project(db_session, project_id=project.id)
    items = _scan_items(db_session, run_id=report.run_id)
    # items 按 id 排序；assert 它们顺序稳定（scan 顺序是 topics → experiments）
    assert len(items) == 3
    svc.claim(db_session, item_id=items[0].id)
    svc.mark_applied(db_session, item_id=items[0].id)
    svc.claim(db_session, item_id=items[1].id)
    svc.mark_failed(db_session, item_id=items[1].id, error="boom")

    payload = svc.build_lkg_payload(
        project_id=project.id, run_id=report.run_id, items=items
    )

    assert payload["schema"] == "fs-migration.last-known-good/v1"
    assert payload["project_id"] == str(project.id)
    assert payload["run_id"] == report.run_id
    # 只有 1 个 applied → 只有 1 个 item（剩下 pending / failed 不进 LKG）
    assert len(payload["items"]) == 1
    # payload item 不带 status 字段（v1 schema），但带 applied_at
    assert "applied_at" in payload["items"][0]
    assert "kind" in payload["items"][0]
    assert "slug" in payload["items"][0]
    assert "content_hash" in payload["items"][0]
    assert "idempotency_key" in payload["items"][0]


def test_diff_against_lkg_no_lkg_returns_empty(db_session):
    """LKG 缺失 → 不阻断；只警告「lkg_present=false」。"""
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="t1", creator_agent_id=agent.id)
    report = svc.scan_project(db_session, project_id=project.id)
    items = _scan_items(db_session, run_id=report.run_id)

    diff = svc.diff_against_lkg(db_items=items, lkg=None)

    assert diff["lkg_present"] is False
    assert diff["lkg_run_id"] is None
    assert diff["in_lkg_only"] == []
    assert diff["in_both"] == []
    # in_db_only 不空（DB item 1 个都没在 LKG 出现过）
    assert diff["in_db_only"] != []
    assert diff["hash_drift"] == []
    assert diff["summary"]["in_db_only"] == 1


def test_diff_against_lkg_in_both_when_hash_matches(db_session):
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="stable", creator_agent_id=agent.id)
    report = svc.scan_project(db_session, project_id=project.id)
    item = db_session.scalar(
        select(MigrationManifestItem).where(MigrationManifestItem.run_id == report.run_id)
    )
    svc.claim(db_session, item_id=item.id)
    svc.mark_applied(db_session, item_id=item.id)
    db_session.refresh(item)

    lkg = svc.build_lkg_payload(
        project_id=project.id, run_id=report.run_id, items=[item]
    )

    diff = svc.diff_against_lkg(db_items=[item], lkg=lkg)

    assert diff["lkg_present"] is True
    assert diff["summary"]["in_both"] == 1
    assert diff["summary"]["in_db_only"] == 0
    assert diff["summary"]["in_lkg_only"] == 0
    assert diff["summary"]["hash_drift"] == 0


def test_diff_against_lkg_in_lkg_only_when_db_deleted(db_session):
    """LKG 里有的 (kind, slug, hash) 现在 DB 里没了 → in_lkg_only。"""
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    _make_topic(db_session, project_id=project.id, slug="vanished", creator_agent_id=agent.id)
    report = svc.scan_project(db_session, project_id=project.id)
    item = db_session.scalar(
        select(MigrationManifestItem).where(MigrationManifestItem.run_id == report.run_id)
    )
    svc.claim(db_session, item_id=item.id)
    svc.mark_applied(db_session, item_id=item.id)
    db_session.refresh(item)

    lkg = svc.build_lkg_payload(
        project_id=project.id, run_id=report.run_id, items=[item]
    )

    # 显式删 manifest item；topic 留着（manifest item 不 cascade topic）
    db_session.delete(item)
    db_session.flush()

    diff = svc.diff_against_lkg(db_items=[], lkg=lkg)

    assert diff["summary"]["in_both"] == 0
    assert diff["summary"]["in_lkg_only"] == 1
    assert diff["in_lkg_only"][0]["slug"] == "vanished"


def test_diff_against_lkg_hash_drift_when_content_changed(db_session):
    """同 (kind, slug) 在 LKG 与 DB 都存在但 hash 不同 → hash_drift。"""
    project = _make_project(db_session)
    agent = _make_agent(db_session, project_id=project.id)
    topic = _make_topic(
        db_session, project_id=project.id, slug="drifter", creator_agent_id=agent.id
    )
    report1 = svc.scan_project(db_session, project_id=project.id)
    item_v1 = db_session.scalar(
        select(MigrationManifestItem).where(MigrationManifestItem.run_id == report1.run_id)
    )
    svc.claim(db_session, item_id=item_v1.id)
    svc.mark_applied(db_session, item_id=item_v1.id)
    db_session.refresh(item_v1)
    lkg = svc.build_lkg_payload(
        project_id=project.id, run_id=report1.run_id, items=[item_v1]
    )

    topic.title = "drifter (revised)"
    db_session.flush()
    report2 = svc.scan_project(db_session, project_id=project.id)
    item_v2 = db_session.scalar(
        select(MigrationManifestItem).where(MigrationManifestItem.run_id == report2.run_id)
    )

    diff = svc.diff_against_lkg(db_items=[item_v2], lkg=lkg)

    assert diff["summary"]["hash_drift"] == 1
    assert diff["hash_drift"][0]["slug"] == "drifter"
    # LKG 里那个旧 hash 应该出现在 hash_drift 条目里供运维对照
    assert item_v1.content_hash in diff["hash_drift"][0]["lkg_content_hashes"]
