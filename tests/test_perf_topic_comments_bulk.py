"""Tests for perf experiment (193a5074) PR2 — topic comments bulk loader.

Verifies the Layer-2 N+1 fix in ``topic_work_items_bundle_for_agent``:

1. ``_topic_comments_bulk`` returns the same per-topic rows as the
   single-topic ``_topic_comments`` helper (ordering + grouping
   preserved).
2. With N open topics, ``topic_work_items_bundle_for_agent`` issues
   at most one ``topic_comments`` SELECT regardless of N — measured via
   the ``count_sql_calls`` fixture from PR1.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event

from server.domain.models import Agent, Project, Topic, TopicComment, TopicStatus
from server.services.topic_work_item_service import (
    _topic_comments,
    _topic_comments_bulk,
    topic_work_items_bundle_for_agent,
)


def _make_topic(db, project: Project, host: Agent, title: str) -> Topic:
    topic = Topic(
        project_id=project.id,
        creator_agent_id=host.id,
        title=title,
        description="perf bulk test",
        status=TopicStatus.open,
    )
    db.add(topic)
    db.flush()
    return topic


def _add_comments(db, topic: Topic, host: Agent, n: int) -> list[TopicComment]:
    rows: list[TopicComment] = []
    for i in range(n):
        c = TopicComment(
            topic_id=topic.id,
            author_agent_id=host.id,
            body=f"comment {i} on {topic.title}",
            kind="user",
        )
        db.add(c)
        rows.append(c)
    db.flush()
    return rows


@pytest.fixture
def perf_bulk_host(db_session):
    project = Project(
        project_key="perf-bulk",
        name="Perf Bulk",
        workspace_path="/tmp/perf-bulk",
    )
    db_session.add(project)
    db_session.flush()
    host = Agent(
        project_id=project.id,
        name="perf-bulk-host",
        role="agent",
        api_token_hash="x" * 64,
        api_token_prefix="perf-bul",
    )
    db_session.add(host)
    db_session.flush()
    return project, host


def test_bulk_matches_single_topic_helper(db_session, perf_bulk_host):
    project, host = perf_bulk_host
    topic = _make_topic(db_session, project, host, "t1")
    _add_comments(db_session, topic, host, 5)
    db_session.commit()
    db_session.expire_all()

    single = _topic_comments(db_session, topic.id)
    bulk = _topic_comments_bulk(db_session, [topic.id])
    assert [c.id for c in bulk[topic.id]] == [c.id for c in single]
    assert [c.body for c in bulk[topic.id]] == [c.body for c in single]


def test_bulk_returns_empty_dict_for_empty_input(db_session):
    assert _topic_comments_bulk(db_session, []) == {}


def test_bulk_groups_by_topic_id(db_session, perf_bulk_host):
    project, host = perf_bulk_host
    t1 = _make_topic(db_session, project, host, "alpha")
    t2 = _make_topic(db_session, project, host, "beta")
    _add_comments(db_session, t1, host, 3)
    _add_comments(db_session, t2, host, 2)
    db_session.commit()
    db_session.expire_all()

    grouped = _topic_comments_bulk(db_session, [t1.id, t2.id])
    assert len(grouped[t1.id]) == 3
    assert len(grouped[t2.id]) == 2
    # topics with no comments get an empty bucket, not absent
    t3 = _make_topic(db_session, project, host, "gamma")
    db_session.commit()
    db_session.expire_all()
    grouped2 = _topic_comments_bulk(db_session, [t1.id, t2.id, t3.id])
    assert grouped2[t3.id] == []


def test_bundle_for_agent_uses_bulk_load_not_n_plus_1(db_session, engine, perf_bulk_host):
    """With 5 open topics, ``topic_work_items_bundle_for_agent`` must NOT issue
    a per-topic SELECT against ``topic_comments``. Allowed budget: 1 (the bulk
    loader itself). Anything more means the N+1 came back."""
    project, host = perf_bulk_host
    topic_ids = []
    for i in range(5):
        t = _make_topic(db_session, project, host, f"topic-{i}")
        _add_comments(db_session, t, host, 2)
        topic_ids.append(t.id)
    db_session.commit()
    db_session.expire_all()

    counter = {"n": 0}

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if "from topic_comments" in statement.lower():
            counter["n"] += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        bundle = topic_work_items_bundle_for_agent(db_session, host)
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
    assert counter["n"] <= 1, (
        f"topic_comments SELECT count = {counter['n']}; expected <=1 (bulk loader). "
        f"N+1 regressed."
    )
    # Sanity: every topic got its comments bucket.
    for tid in topic_ids:
        assert len(bundle.comments_by_topic[tid]) == 2
