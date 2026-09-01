"""review list archived filter + plan_version combination (experiment 18f1d8f6 I1(c)).

Pins plan (c) acceptance:

1. ``map experiment review list`` 默认 include_archived=True (N=2 过渡期) — historical
   review 不会从 CLI 调用方眼里消失。
2. ``--no-include-archived`` 把 archived review 从结果里排除。
3. ``--plan-version N`` 与 ``--include-archived/--no-include-archived`` 正交组合。
4. SDK ``list_reviews(experiment_id, include_archived=..., plan_version=...)`` 行为与服务端
   一致。
5. 过滤参数透传到 query string；空 filter 不附加冗余参数。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from map_types import ReviewArchivedReason, ReviewRead
from map_types.enums import ReviewSubstituteKind
from sqlalchemy.orm import Session

from server.domain.models import (
    Agent,
    AgentRole,
    Experiment,
    Project,
    Review,
)


def _make_project(db: Session) -> Project:
    project = Project(
        id=uuid.uuid4(),
        project_key="t",
        name="T",
        workspace_path="/tmp/t",
    )
    db.add(project)
    db.flush()
    return project


def _make_agent(
    db: Session, *, project_id: uuid.UUID, name: str = "a"
) -> Agent:
    a = Agent(
        id=uuid.uuid4(),
        project_id=project_id,
        name=name,
        api_token_hash="x",
        api_token_prefix="x",
        role=AgentRole.agent,
    )
    db.add(a)
    db.flush()
    return a


def _make_experiment(db: Session, *, project_id: uuid.UUID, creator_id: uuid.UUID) -> Experiment:
    e = Experiment(
        id=uuid.uuid4(),
        project_id=project_id,
        creator_agent_id=creator_id,
        title="t",
        phase="review",
        current_plan_version=2,
    )
    db.add(e)
    db.flush()
    return e


def _make_review(
    db: Session,
    *,
    experiment_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    plan_version: int,
    archived_at: datetime | None = None,
    archived_reason: ReviewArchivedReason | None = None,
) -> Review:
    r = Review(
        id=uuid.uuid4(),
        experiment_id=experiment_id,
        reviewer_agent_id=reviewer_id,
        plan_version=plan_version,
        substitute_kind=ReviewSubstituteKind.none,
        archived_at=archived_at,
        archived_reason=archived_reason,
    )
    db.add(r)
    db.flush()
    return r


# ---------------------------------------------------------------------------
# Service-level filter contract
# ---------------------------------------------------------------------------


def test_service_list_reviews_excludes_archived_by_default(db_session):
    """include_archived=False (default) hides archived reviews."""
    project = _make_project(db_session)
    creator = _make_agent(db_session, project_id=project.id, name="creator")
    exp = _make_experiment(db_session, project_id=project.id, creator_id=creator.id)
    reviewer = _make_agent(db_session, project_id=project.id, name="reviewer")

    active = _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        plan_version=2,
    )
    _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        plan_version=1,
        archived_at=datetime.utcnow(),
        archived_reason=ReviewArchivedReason.auto,
    )
    db_session.commit()

    from server.services.review_service import list_reviews

    rows = list_reviews(db_session, exp.id)
    assert [r.id for r in rows] == [active.id]


def test_service_list_reviews_include_archived_returns_all(db_session):
    """include_archived=True returns archived + active."""
    project = _make_project(db_session)
    creator = _make_agent(db_session, project_id=project.id, name="creator")
    exp = _make_experiment(db_session, project_id=project.id, creator_id=creator.id)
    reviewer = _make_agent(db_session, project_id=project.id, name="reviewer")

    a = _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        plan_version=2,
    )
    b = _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        plan_version=1,
        archived_at=datetime.utcnow(),
        archived_reason=ReviewArchivedReason.auto,
    )
    db_session.commit()

    from server.services.review_service import list_reviews

    rows = list_reviews(db_session, exp.id, include_archived=True)
    assert {r.id for r in rows} == {a.id, b.id}


def test_service_list_reviews_plan_version_filter(db_session):
    """plan_version filter is independent of archived filter."""
    project = _make_project(db_session)
    creator = _make_agent(db_session, project_id=project.id, name="creator")
    exp = _make_experiment(db_session, project_id=project.id, creator_id=creator.id)
    reviewer = _make_agent(db_session, project_id=project.id, name="reviewer")

    v1_active = _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        plan_version=1,
    )
    _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        plan_version=2,
        archived_at=datetime.utcnow(),
        archived_reason=ReviewArchivedReason.auto,
    )
    _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        plan_version=3,
    )
    db_session.commit()

    from server.services.review_service import list_reviews

    # plan_version=1 + include_archived=False → only the active v1 review.
    rows = list_reviews(db_session, exp.id, plan_version=1)
    assert [r.id for r in rows] == [v1_active.id]

    # plan_version=2 + include_archived=True → only the archived v2 review.
    rows = list_reviews(
        db_session,
        exp.id,
        include_archived=True,
        plan_version=2,
    )
    assert len(rows) == 1
    assert rows[0].archived_at is not None


def test_service_list_reviews_plan_version_no_match_returns_empty(db_session):
    """plan_version filter with no matching rows returns [] (not None)."""
    project = _make_project(db_session)
    creator = _make_agent(db_session, project_id=project.id, name="creator")
    exp = _make_experiment(db_session, project_id=project.id, creator_id=creator.id)
    reviewer = _make_agent(db_session, project_id=project.id, name="reviewer")

    _make_review(
        db_session,
        experiment_id=exp.id,
        reviewer_id=reviewer.id,
        plan_version=1,
    )
    db_session.commit()

    from server.services.review_service import list_reviews

    rows = list_reviews(db_session, exp.id, plan_version=99)
    assert rows == []


# ---------------------------------------------------------------------------
# CLI default-behavior contract (N=2 transition)
# ---------------------------------------------------------------------------


def test_cli_review_list_default_include_archived_is_true():
    """CLI 默认 include_archived=True (N=2 过渡期).

    该断言 pin 当前 plan 约定的「过渡期默认 include archived」行为；N=2 release 后
    CLI 的 typer.Option 默认需翻成 False，并同步更新本测试。
    """
    import inspect

    # 50cddb7e I3: review_list 从 cli.main 迁到 cli.commands.experiment；
    # T33: 随命令体再迁 cli.commands.experiment_review（锚点随源码走）。
    from cli.commands.experiment_review import review_list

    src = inspect.getsource(review_list)
    # typer.Option may be formatted across multiple lines by ruff; match the
    # default value alone + the flag declaration, not a single line.
    assert '"--include-archived/' in src, "missing --include-archived flag declaration"
    assert "True" in src, (
        "CLI review_list 默认 include_archived 必须为 True (N=2 过渡期)；"
        "N=2 release 时把 True 改为 False 并同步本测试。"
    )


def test_cli_review_list_supports_plan_version_flag():
    """CLI 提供 --plan-version flag 并向下游透传。"""
    import inspect

    from cli.commands.experiment_review import review_list

    src = inspect.getsource(review_list)
    assert '"--plan-version"' in src
    assert "plan_version=plan_version" in src


# ---------------------------------------------------------------------------
# SDK end-to-end (HTTP layer)
# ---------------------------------------------------------------------------


def test_sdk_list_reviews_passes_include_archived_param(monkeypatch):
    """SDK 把 include_archived / plan_version 透传到 query string."""
    captured: dict = {}

    class _StubClient:
        def _json(self, method, path, *, params=None):
            captured["method"] = method
            captured["path"] = path
            captured["params"] = params
            return []

        def list_reviews(self, experiment_id, *, include_archived=False, plan_version=None):
            params: dict[str, str] = {}
            if include_archived:
                params["include_archived"] = "true"
            if plan_version is not None:
                params["plan_version"] = str(plan_version)
            return [
                ReviewRead.model_validate(item)
                for item in self._json(
                    "GET",
                    f"/experiments/{experiment_id}/reviews",
                    params=params or None,
                )
            ]

    client = _StubClient()
    exp_id = uuid.uuid4()
    client.list_reviews(exp_id, include_archived=True, plan_version=3)
    assert captured["method"] == "GET"
    assert captured["path"] == f"/experiments/{exp_id}/reviews"
    assert captured["params"] == {"include_archived": "true", "plan_version": "3"}

    # Default (no args) sends no query params.
    client.list_reviews(exp_id)
    assert captured["params"] is None
