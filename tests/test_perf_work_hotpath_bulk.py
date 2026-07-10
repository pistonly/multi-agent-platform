"""Perf regression: get_agent_work / get_todos hot-path bulk loaders.

Pins three Layer-2 N+1 fixes on the waker hot path:

1. Open topics are loaded once via the work-items bundle and reused by
   ``get_todos`` / ``list_topic_progress_for_agent`` /
   ``list_pending_advance_rounds`` / ``list_stale_open_topics``.
2. ``advance_round_ack_state`` accepts preloaded comments so
   ``list_pending_advance_rounds`` issues zero extra ``topic_comments``
   SELECTs when a bundle is available.
3. ``prior_version_reviews_fully_resolved_by_experiment`` issues at most
   one ``reviews`` SELECT for the whole pending_reviews candidate set.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import event

from server.domain.models import (
    Agent,
    AgentRole,
    Experiment,
    ExperimentPhase,
    Project,
    Review,
    ReviewItem,
    ReviewItemKind,
    ReviewItemStatus,
    Topic,
    TopicComment,
    TopicStatus,
)
from server.domain.topic_ack_constants import ACK_ACCEPT_MARKER
from server.services.agent_work_service import get_agent_work
from server.services.review_service import (
    _prior_version_reviews_fully_resolved,
    prior_version_reviews_fully_resolved_by_experiment,
)
from server.services.todo_service import get_todos, list_pending_advance_rounds
from server.services.topic_work_item_service import topic_work_items_bundle_for_agent


def _make_project(db, key: str = "perf-hotpath") -> Project:
    project = Project(
        project_key=key,
        name="Perf Hotpath",
        workspace_path="/tmp/perf-hotpath",
    )
    db.add(project)
    db.flush()
    return project


def _make_agent(db, project: Project, name: str, role: AgentRole = AgentRole.agent) -> Agent:
    agent = Agent(
        project_id=project.id,
        name=name,
        role=role,
        api_token_hash="x" * 64,
        api_token_prefix=name[:8],
    )
    db.add(agent)
    db.flush()
    return agent


def _make_topic(db, project: Project, host: Agent, title: str) -> Topic:
    topic = Topic(
        project_id=project.id,
        creator_agent_id=host.id,
        title=title,
        description="perf hotpath",
        status=TopicStatus.open,
        advance_round_pending_since=datetime.now(UTC) - timedelta(hours=1),
    )
    db.add(topic)
    db.flush()
    return topic


def _add_comment(
    db,
    topic: Topic,
    author: Agent,
    body: str,
    *,
    parent: TopicComment | None = None,
) -> TopicComment:
    comment = TopicComment(
        topic_id=topic.id,
        author_agent_id=author.id,
        body=body,
        parent_comment_id=parent.id if parent else None,
        comment_seq=1,
    )
    db.add(comment)
    db.flush()
    return comment


def _count_sql(db, predicate) -> list[str]:
    statements: list[str] = []

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if predicate(statement):
            statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", _before_cursor_execute)
    return statements


def test_get_agent_work_loads_open_topics_once(db_session):
    project = _make_project(db_session)
    host = _make_agent(db_session, project, "host-hotpath")
    participant = _make_agent(db_session, project, "participant-hotpath")
    for i in range(4):
        topic = _make_topic(db_session, project, host, f"topic-{i}")
        _add_comment(db_session, topic, host, f"## Round 1 Summary\nseed {i}")
        _add_comment(
            db_session,
            topic,
            participant,
            f"Participant round acknowledgement ({ACK_ACCEPT_MARKER}).",
        )
    db_session.commit()

    topic_selects: list[str] = []

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        lowered = statement.lower()
        if "from topics" in lowered or "from \"topics\"" in lowered:
            topic_selects.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", _before_cursor_execute)
    try:
        get_agent_work(db_session, host)
    finally:
        event.remove(bind, "before_cursor_execute", _before_cursor_execute)

    assert len(topic_selects) <= 1, (
        f"expected <=1 open-topics SELECT on get_agent_work, got {len(topic_selects)}:\n"
        + "\n".join(topic_selects)
    )


def test_list_pending_advance_rounds_reuses_preloaded_comments(db_session):
    project = _make_project(db_session, key="perf-ack-bulk")
    host = _make_agent(db_session, project, "host-ack")
    participant = _make_agent(db_session, project, "participant-ack")
    for i in range(3):
        topic = _make_topic(db_session, project, host, f"ack-topic-{i}")
        _add_comment(db_session, topic, host, f"## Round 1 Summary\nack {i}")
        _add_comment(
            db_session,
            topic,
            participant,
            f"Participant round acknowledgement ({ACK_ACCEPT_MARKER}).",
        )
    db_session.commit()

    bundle = topic_work_items_bundle_for_agent(db_session, host)
    comment_selects: list[str] = []

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        lowered = statement.lower()
        if "from topic_comments" in lowered or "from \"topic_comments\"" in lowered:
            comment_selects.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", _before_cursor_execute)
    try:
        pending = list_pending_advance_rounds(
            db_session,
            host,
            open_topics=bundle.open_topics,
            comments_by_topic=bundle.comments_by_topic,
        )
    finally:
        event.remove(bind, "before_cursor_execute", _before_cursor_execute)

    assert len(pending) == 3
    assert comment_selects == [], (
        "list_pending_advance_rounds must not re-query topic_comments when "
        f"comments_by_topic is provided; got {len(comment_selects)}"
    )


def test_prior_version_reviews_batch_matches_single_and_one_select(db_session):
    project = _make_project(db_session, key="perf-prior-reviews")
    host = _make_agent(db_session, project, "host-prior")
    reviewer = _make_agent(db_session, project, "reviewer-prior")
    experiments: list[Experiment] = []
    for i in range(5):
        exp = Experiment(
            project_id=project.id,
            creator_agent_id=host.id,
            title=f"exp-{i}",
            description="prior review bulk",
            phase=ExperimentPhase.review,
            current_plan_version=2,
        )
        db_session.add(exp)
        db_session.flush()
        review = Review(
            experiment_id=exp.id,
            reviewer_agent_id=reviewer.id,
            plan_version=1,
            substitute_kind="none",
        )
        db_session.add(review)
        db_session.flush()
        db_session.add(
            ReviewItem(
                review_id=review.id,
                kind=ReviewItemKind.unreasonable,
                content=f"fix me {i}",
                status=ReviewItemStatus.resolved,
            )
        )
        experiments.append(exp)
    db_session.commit()

    single = {
        exp.id: _prior_version_reviews_fully_resolved(db_session, exp) for exp in experiments
    }

    review_selects: list[str] = []

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        lowered = statement.lower()
        if "from reviews" in lowered or "from \"reviews\"" in lowered:
            review_selects.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", _before_cursor_execute)
    try:
        batched = prior_version_reviews_fully_resolved_by_experiment(db_session, experiments)
    finally:
        event.remove(bind, "before_cursor_execute", _before_cursor_execute)

    assert batched == single
    assert all(batched.values())
    assert len(review_selects) == 1, (
        f"expected exactly 1 reviews SELECT, got {len(review_selects)}:\n"
        + "\n".join(review_selects)
    )


def test_get_todos_with_bundle_keeps_pending_reviews_semantics(db_session):
    """Smoke: get_todos(bundle=...) still builds pending_reviews via the batch helper."""
    project = _make_project(db_session, key="perf-todos-bundle")
    host = _make_agent(db_session, project, "host-todos")
    reviewer = _make_agent(db_session, project, "reviewer-todos")
    topic = _make_topic(db_session, project, host, "todos-topic")
    _add_comment(db_session, topic, host, "## Round 1 Summary\ntodos")
    exp = Experiment(
        project_id=project.id,
        creator_agent_id=host.id,
        title="review-exp",
        description="todos bundle",
        phase=ExperimentPhase.review,
        current_plan_version=1,
    )
    db_session.add(exp)
    db_session.commit()

    bundle = topic_work_items_bundle_for_agent(db_session, reviewer)
    todos = get_todos(db_session, reviewer, bundle=bundle)
    assert any(item.id == exp.id for item in todos.pending_reviews)
