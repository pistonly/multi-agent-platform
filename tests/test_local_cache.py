"""Unit tests for cli/local_cache.py — SQLite cache for offline browsing.

Tests use MagicMock for the MAPClient to avoid needing a live server.
The focus is on cache initialization, pull (upsert) correctness, and
offline read operations (status / list / get).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cli.local_cache import (
    CachedExperiment,
    CachedTopic,
    SyncMeta,
    get_cache_path,
    get_cached_topic,
    get_sync_status,
    init_cache,
    list_cached_topics,
    pull_project_to_cache,
)


# ---------------------------------------------------------------------------
# Fixtures: build mock SDK objects matching the Pydantic schema shapes
# ---------------------------------------------------------------------------


def _make_topic(
    *,
    title: str = "Discuss API design",
    status: str = "resolved",
    description: str = "We need to decide on the API.",
) -> MagicMock:
    topic = MagicMock()
    topic.id = uuid.uuid4()
    topic.title = title
    topic.status = MagicMock()
    topic.status.value = status
    topic.discussion_round = MagicMock()
    topic.discussion_round.value = "round2"
    topic.creator_name = "host-agent"
    topic.creator_agent_id = uuid.uuid4()
    topic.created_at = datetime(2026, 1, 10, 9, 0, tzinfo=timezone.utc)
    topic.description = description
    topic.comments = []
    topic.decision = None
    topic.experiments = []
    topic.model_dump = MagicMock(return_value={
        "id": str(topic.id),
        "title": title,
        "status": status,
        "description": description,
    })
    return topic


def _make_experiment_bundle(
    *,
    title: str = "Test caching layer",
    phase: str = "completed",
) -> MagicMock:
    bundle = MagicMock()
    exp = MagicMock()
    exp.id = uuid.uuid4()
    exp.title = title
    exp.phase = MagicMock()
    exp.phase.value = phase
    exp.creator_agent_id = uuid.uuid4()
    exp.created_at = datetime(2026, 1, 12, 8, 0, tzinfo=timezone.utc)
    exp.updated_at = datetime(2026, 1, 18, 16, 0, tzinfo=timezone.utc)
    exp.acceptance_status = []

    bundle.experiment = exp
    bundle.plans = []
    bundle.reviews = []
    bundle.comments = []
    bundle.logs = []
    bundle.model_dump = MagicMock(return_value={
        "experiment": {"id": str(exp.id), "title": title, "phase": phase},
        "plans": [],
        "reviews": [],
    })
    return bundle


def _make_client(
    topics: list,
    experiment_bundles: list,
) -> MagicMock:
    """Create a mock MAPClient with paginated list + detail methods."""
    client = MagicMock()

    # Topics: paginated list + get_topic by id
    def list_topics_side_effect(project_id, *, page, page_size, include_archived):
        if page == 1:
            return topics
        return []
    client.list_topics.side_effect = list_topics_side_effect

    topic_map = {t.id: t for t in topics}
    client.get_topic = lambda tid: topic_map.get(tid)

    # Experiments: paginated list + get_experiment_bundle by id
    exp_summaries = [b.experiment for b in experiment_bundles]
    def list_experiments_side_effect(project_id, *, page, page_size, include_archived):
        if page == 1:
            return exp_summaries
        return []
    client.list_experiments.side_effect = list_experiments_side_effect

    bundle_map = {b.experiment.id: b for b in experiment_bundles}
    client.get_experiment_bundle = lambda eid: bundle_map.get(eid)

    return client


# ---------------------------------------------------------------------------
# Tests: cache initialization
# ---------------------------------------------------------------------------


class TestInitCache:
    def test_creates_db_file(self, tmp_path):
        """init_cache should create the SQLite DB with all tables."""
        db_path = tmp_path / "cache.db"
        session = init_cache(db_path)

        assert db_path.exists()
        # Tables should exist
        assert session.query(SyncMeta).count() == 0
        assert session.query(CachedTopic).count() == 0
        assert session.query(CachedExperiment).count() == 0
        session.close()

    def test_reopens_existing_db(self, tmp_path):
        """init_cache on an existing DB should not wipe data."""
        db_path = tmp_path / "cache.db"
        session1 = init_cache(db_path)
        session1.add(SyncMeta(
            project_id="test-id",
            project_key="test-key",
            last_pull_at=datetime.now(timezone.utc),
            topic_count="5",
            experiment_count="2",
        ))
        session1.commit()
        session1.close()

        session2 = init_cache(db_path)
        metas = session2.query(SyncMeta).all()
        assert len(metas) == 1
        assert metas[0].project_key == "test-key"
        session2.close()


class TestGetCachePath:
    def test_returns_map_dir_child(self, tmp_path):
        map_dir = tmp_path / ".map"
        map_dir.mkdir()
        result = get_cache_path(map_dir)
        assert result == map_dir / "cache.db"


# ---------------------------------------------------------------------------
# Tests: pull_project_to_cache
# ---------------------------------------------------------------------------


class TestPullProjectToCache:
    def test_empty_project(self, tmp_path):
        """Pulling a project with no topics/experiments should still write sync_meta."""
        db_path = tmp_path / "cache.db"
        session = init_cache(db_path)
        client = _make_client([], [])
        pid = uuid.uuid4()
        pk = "test-project"

        result = pull_project_to_cache(session, client, pid, pk)

        assert result == {"topics": 0, "experiments": 0}
        metas = session.query(SyncMeta).all()
        assert len(metas) == 1
        assert metas[0].project_key == pk
        assert metas[0].topic_count == "0"
        assert metas[0].experiment_count == "0"
        session.close()

    def test_pull_topics_and_experiments(self, tmp_path):
        """Pulling should cache all topics and experiments."""
        db_path = tmp_path / "cache.db"
        session = init_cache(db_path)
        topics = [_make_topic(title="Topic A"), _make_topic(title="Topic B")]
        bundles = [_make_experiment_bundle(title="Exp 1")]
        client = _make_client(topics, bundles)
        pid = uuid.uuid4()

        result = pull_project_to_cache(session, client, pid, "test-key")

        assert result["topics"] == 2
        assert result["experiments"] == 1
        assert session.query(CachedTopic).count() == 2
        assert session.query(CachedExperiment).count() == 1

        # Verify sync_meta
        meta = session.query(SyncMeta).filter(SyncMeta.project_id == str(pid)).one()
        assert meta.topic_count == "2"
        assert meta.experiment_count == "1"
        session.close()

    def test_upsert_updates_existing(self, tmp_path):
        """Re-pulling should update existing rows, not duplicate."""
        db_path = tmp_path / "cache.db"
        session = init_cache(db_path)
        topic = _make_topic(title="Original Title")
        client = _make_client([topic], [])
        pid = uuid.uuid4()

        # First pull
        pull_project_to_cache(session, client, pid, "key")
        assert session.query(CachedTopic).count() == 1
        first_pulled = session.query(CachedTopic).one().pulled_at

        # Second pull with same topic (simulating title change)
        topic.title = "Updated Title"
        pull_project_to_cache(session, client, pid, "key")
        assert session.query(CachedTopic).count() == 1  # still 1, not 2

        cached = session.query(CachedTopic).one()
        assert cached.title == "Updated Title"
        assert cached.pulled_at >= first_pulled
        session.close()

    def test_data_json_contains_full_topic(self, tmp_path):
        """The cached JSON blob should contain the full topic data."""
        db_path = tmp_path / "cache.db"
        session = init_cache(db_path)
        topic = _make_topic(title="My Topic", description="Important discussion")
        client = _make_client([topic], [])
        pid = uuid.uuid4()

        pull_project_to_cache(session, client, pid, "key")
        cached = session.query(CachedTopic).one()
        data = json.loads(cached.data_json)
        assert data["title"] == "My Topic"
        assert data["description"] == "Important discussion"
        session.close()

    def test_data_json_contains_full_experiment(self, tmp_path):
        """The cached JSON blob should contain the full experiment bundle."""
        db_path = tmp_path / "cache.db"
        session = init_cache(db_path)
        bundle = _make_experiment_bundle(title="My Experiment", phase="running")
        client = _make_client([], [bundle])
        pid = uuid.uuid4()

        pull_project_to_cache(session, client, pid, "key")
        cached = session.query(CachedExperiment).one()
        data = json.loads(cached.data_json)
        assert data["experiment"]["title"] == "My Experiment"
        assert data["experiment"]["phase"] == "running"
        session.close()


# ---------------------------------------------------------------------------
# Tests: read operations
# ---------------------------------------------------------------------------


class TestGetSyncStatus:
    def test_no_records(self, tmp_path):
        """Empty cache should return empty list."""
        session = init_cache(tmp_path / "cache.db")
        assert get_sync_status(session) == []
        session.close()

    def test_returns_all_projects(self, tmp_path):
        """Should return status for all cached projects."""
        session = init_cache(tmp_path / "cache.db")
        now = datetime.now(timezone.utc)
        for i in range(2):
            session.add(SyncMeta(
                project_id=f"proj-{i}",
                project_key=f"key-{i}",
                last_pull_at=now,
                topic_count=str(i + 1),
                experiment_count=str(i),
            ))
        session.commit()

        statuses = get_sync_status(session)
        assert len(statuses) == 2
        session.close()

    def test_filter_by_project(self, tmp_path):
        """Should filter by project_id when provided."""
        session = init_cache(tmp_path / "cache.db")
        now = datetime.now(timezone.utc)
        session.add(SyncMeta(
            project_id="proj-a",
            project_key="key-a",
            last_pull_at=now,
            topic_count="3",
            experiment_count="1",
        ))
        session.add(SyncMeta(
            project_id="proj-b",
            project_key="key-b",
            last_pull_at=now,
            topic_count="5",
            experiment_count="2",
        ))
        session.commit()

        statuses = get_sync_status(session, uuid.UUID("00000000-0000-0000-0000-000000000001"))
        assert len(statuses) == 0  # no match

        statuses = get_sync_status(session, uuid.UUID("00000000-0000-0000-0000-000000000000"))
        # Still no match since our IDs are "proj-a"/"proj-b" not UUIDs
        assert len(statuses) == 0
        session.close()


class TestListCachedTopics:
    def test_empty(self, tmp_path):
        session = init_cache(tmp_path / "cache.db")
        assert list_cached_topics(session, uuid.uuid4()) == []
        session.close()

    def test_returns_lightweight_metadata(self, tmp_path):
        """Should return id/title/status/pulled_at without the full JSON blob."""
        session = init_cache(tmp_path / "cache.db")
        pid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        session.add(CachedTopic(
            id=str(uuid.uuid4()),
            project_id=str(pid),
            title="Topic A",
            status="open",
            data_json='{"key": "value"}',
            pulled_at=now,
        ))
        session.commit()

        topics = list_cached_topics(session, pid)
        assert len(topics) == 1
        assert topics[0]["title"] == "Topic A"
        assert topics[0]["status"] == "open"
        assert "data" not in topics[0]  # no full blob
        session.close()

    def test_filters_by_project(self, tmp_path):
        """Should only return topics for the specified project."""
        session = init_cache(tmp_path / "cache.db")
        pid1 = uuid.uuid4()
        pid2 = uuid.uuid4()
        now = datetime.now(timezone.utc)
        for pid in [pid1, pid1, pid2]:
            session.add(CachedTopic(
                id=str(uuid.uuid4()),
                project_id=str(pid),
                title="Topic",
                status="open",
                data_json="{}",
                pulled_at=now,
            ))
        session.commit()

        assert len(list_cached_topics(session, pid1)) == 2
        assert len(list_cached_topics(session, pid2)) == 1
        session.close()


class TestGetCachedTopic:
    def test_not_found(self, tmp_path):
        session = init_cache(tmp_path / "cache.db")
        assert get_cached_topic(session, uuid.uuid4()) is None
        session.close()

    def test_returns_full_data(self, tmp_path):
        """Should return the full JSON data blob."""
        session = init_cache(tmp_path / "cache.db")
        tid = uuid.uuid4()
        pid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        payload = {"id": str(tid), "title": "Full Topic", "comments": [{"body": "hi"}]}
        session.add(CachedTopic(
            id=str(tid),
            project_id=str(pid),
            title="Full Topic",
            status="resolved",
            data_json=json.dumps(payload),
            pulled_at=now,
        ))
        session.commit()

        result = get_cached_topic(session, tid)
        assert result is not None
        assert result["title"] == "Full Topic"
        assert result["status"] == "resolved"
        assert result["data"]["title"] == "Full Topic"
        assert result["data"]["comments"][0]["body"] == "hi"
        session.close()
