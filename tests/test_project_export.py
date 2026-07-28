"""Unit tests for cli/project_export.py — Markdown snapshot export.

Tests use MagicMock for the MAPClient to avoid needing a live server.
The focus is on rendering correctness: comment tree nesting, decision
formatting, experiment bundle completeness, slug generation, and
INDEX.md structure.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cli.project_export import export_project_history, _slugify, _fmt_dt


# ---------------------------------------------------------------------------
# Fixtures: build mock SDK objects matching the Pydantic schema shapes
# ---------------------------------------------------------------------------


def _make_comment(
    *,
    author_name: str = "alice",
    body: str = "I agree.",
    kind: str = "user",
    children: list | None = None,
    comment_seq: int = 1,
) -> MagicMock:
    node = MagicMock()
    node.id = uuid.uuid4()
    node.topic_id = uuid.uuid4()
    node.author_agent_id = uuid.uuid4()
    node.author_name = author_name
    node.parent_comment_id = None
    node.body = body
    node.kind = kind
    node.comment_seq = comment_seq
    node.created_at = datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc)
    node.unresolved_mentions = []
    node.children = children or []
    return node


def _make_topic(
    *,
    title: str = "Discuss API design",
    status: str = "resolved",
    description: str = "We need to decide on the API.",
    comments: list | None = None,
    decision: dict | None = None,
    experiments: list | None = None,
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
    topic.comments = comments or []
    topic.decision = decision
    topic.experiments = experiments or []
    return topic


def _make_decision(
    *,
    decision: str = "Use Option B",
    rationale: str = "Option B is more maintainable.",
    action_items: list | None = None,
) -> MagicMock:
    dec = MagicMock()
    dec.id = uuid.uuid4()
    dec.decision = decision
    dec.rationale = rationale
    dec.rejected_options = None
    dec.open_questions = None
    dec.no_decision_reason = None
    dec.action_items = action_items or []
    dec.created_at = datetime(2026, 1, 20, 14, 0, tzinfo=timezone.utc)
    dec.updated_at = datetime(2026, 1, 20, 14, 0, tzinfo=timezone.utc)
    return dec


def _make_experiment_bundle(
    *,
    title: str = "Test caching layer",
    phase: str = "completed",
    description: str = "Evaluate Redis vs in-memory cache.",
) -> MagicMock:
    bundle = MagicMock()
    exp = MagicMock()
    exp.id = uuid.uuid4()
    exp.title = title
    exp.phase = MagicMock()
    exp.phase.value = phase
    exp.creator_agent_id = uuid.uuid4()
    exp.current_plan_version = 2
    exp.created_at = datetime(2026, 1, 12, 8, 0, tzinfo=timezone.utc)
    exp.updated_at = datetime(2026, 1, 18, 16, 0, tzinfo=timezone.utc)
    exp.topic_id = uuid.uuid4()
    exp.description = description
    exp.acceptance_status = []

    plan = MagicMock()
    plan.version = 1
    plan.author_agent_id = uuid.uuid4()
    plan.created_at = datetime(2026, 1, 12, 8, 0, tzinfo=timezone.utc)
    plan.content_md = "## Plan\n\n- Add Redis\n- Benchmark"

    review = MagicMock()
    review.reviewer_agent_id = uuid.uuid4()
    review.status = "reasonable"
    review.created_at = datetime(2026, 1, 13, 10, 0, tzinfo=timezone.utc)
    review.summary = "Looks good."
    review.items = []

    log = MagicMock()
    log.author_agent_id = uuid.uuid4()
    log.created_at = datetime(2026, 1, 18, 16, 0, tzinfo=timezone.utc)
    log.content_md = "Benchmark complete. Redis 3x faster."

    bundle.experiment = exp
    bundle.plans = [plan]
    bundle.reviews = [review]
    bundle.comments = []
    bundle.logs = [log]
    return bundle


def _make_client(
    topics: list,
    experiment_bundles: list,
) -> MagicMock:
    """Create a mock MAPClient with paginated list + detail methods."""
    client = MagicMock()

    # list_topics returns a page; get_topic returns full detail
    client.list_topics.return_value = topics
    client.get_topic = lambda summary: topics[0] if topics else None  # simplified

    # For paginated calls, return first page then empty
    def list_topics_side_effect(project_id, *, page, page_size, include_archived):
        if page == 1:
            return topics
        return []
    client.list_topics.side_effect = list_topics_side_effect

    # get_topic returns by matching
    topic_map = {t.id: t for t in topics}
    client.get_topic = lambda tid: topic_map.get(tid)

    # Experiments
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
# Tests
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic(self):
        assert _slugify("Discuss API Design") == "discuss-api-design"

    def test_special_chars(self):
        assert _slugify("Bug: `import` fails!") == "bug-import-fails"

    def test_empty(self):
        assert _slugify("") == "untitled"

    def test_truncation(self):
        long = "A" * 200
        assert len(_slugify(long)) == 80


class TestFmtDt:
    def test_none(self):
        assert _fmt_dt(None) == "—"

    def test_datetime(self):
        dt = datetime(2026, 1, 15, 10, 30, tzinfo=timezone.utc)
        assert "2026-01-15 10:30" in _fmt_dt(dt)


class TestExportProjectHistory:
    def test_empty_project(self, tmp_path):
        """Export with no topics or experiments should still create INDEX.md."""
        client = _make_client([], [])
        result = export_project_history(client, uuid.uuid4(), tmp_path)

        assert (result / "INDEX.md").exists()
        assert (result / "topics").is_dir()
        assert (result / "experiments").is_dir()
        index = (result / "INDEX.md").read_text()
        assert "Topics (0)" in index
        assert "Experiments (0)" in index

    def test_single_topic_no_comments(self, tmp_path):
        """A topic with no comments/decision still exports correctly."""
        topic = _make_topic(comments=[], decision=None)
        client = _make_client([topic], [])
        result = export_project_history(client, uuid.uuid4(), tmp_path)

        topic_files = list((result / "topics").glob("*.md"))
        assert len(topic_files) == 1
        content = topic_files[0].read_text()
        assert "Discuss API design" in content
        assert "resolved" in content

    def test_topic_with_comment_tree(self, tmp_path):
        """Nested comments should be rendered with indentation."""
        child = _make_comment(
            author_name="bob",
            body="I disagree slightly.",
            comment_seq=2,
        )
        parent = _make_comment(
            author_name="alice",
            body="Here is my proposal.",
            children=[child],
            comment_seq=1,
        )
        topic = _make_topic(comments=[parent])
        client = _make_client([topic], [])
        result = export_project_history(client, uuid.uuid4(), tmp_path)

        content = list((result / "topics").glob("*.md"))[0].read_text()
        assert "alice" in content
        assert "Here is my proposal" in content
        assert "bob" in content
        assert "I disagree slightly" in content

    def test_topic_with_decision(self, tmp_path):
        """Decision and action items should be rendered."""
        action_item = MagicMock()
        action_item.title = "Implement Option B"
        action_item.status = MagicMock()
        action_item.status.value = "open"
        action_item.owner_name = "alice"
        action_item.due_at = datetime(2026, 2, 1, tzinfo=timezone.utc)

        decision = _make_decision(action_items=[action_item])
        topic = _make_topic(decision=decision)
        client = _make_client([topic], [])
        result = export_project_history(client, uuid.uuid4(), tmp_path)

        content = list((result / "topics").glob("*.md"))[0].read_text()
        assert "Use Option B" in content
        assert "Implement Option B" in content
        assert "Option B is more maintainable" in content

    def test_experiment_bundle(self, tmp_path):
        """Experiment bundle should include plans, reviews, and logs."""
        bundle = _make_experiment_bundle()
        client = _make_client([], [bundle])
        result = export_project_history(client, uuid.uuid4(), tmp_path)

        exp_files = list((result / "experiments").glob("*.md"))
        assert len(exp_files) == 1
        content = exp_files[0].read_text()
        assert "Test caching layer" in content
        assert "completed" in content
        assert "Add Redis" in content  # plan content
        assert "Looks good" in content  # review summary
        assert "Benchmark complete" in content  # log content

    def test_index_links(self, tmp_path):
        """INDEX.md should contain links to topic and experiment files."""
        topic = _make_topic(title="My Topic")
        bundle = _make_experiment_bundle(title="My Experiment")
        client = _make_client([topic], [bundle])
        result = export_project_history(client, uuid.uuid4(), tmp_path)

        index = (result / "INDEX.md").read_text()
        assert "topics/my-topic.md" in index
        assert "experiments/my-experiment.md" in index

    def test_multiple_topics_and_experiments(self, tmp_path):
        """Multiple items should each get their own file."""
        topics = [
            _make_topic(title="Topic One"),
            _make_topic(title="Topic Two"),
        ]
        bundles = [
            _make_experiment_bundle(title="Exp One"),
            _make_experiment_bundle(title="Exp Two"),
        ]
        client = _make_client(topics, bundles)
        result = export_project_history(client, uuid.uuid4(), tmp_path)

        assert len(list((result / "topics").glob("*.md"))) == 2
        assert len(list((result / "experiments").glob("*.md"))) == 2
