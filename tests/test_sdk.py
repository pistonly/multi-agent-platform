import uuid

import pytest
from fastapi.testclient import TestClient
from map_client import MAPClient, MAPHTTPError
from map_client.testing import MAPTestClientTransport

from server.domain.models import ExperimentPhase
from server.domain.schemas import (
    ExperimentComplete,
    ExperimentCreate,
    ExperimentResultDecision,
    PlanInput,
    ReviewCreate,
)

pytestmark = pytest.mark.slow
from map_types.schemas import TopicActionItemCreate, TopicResolve

from tests._frontmatter import make_valid_plan


def test_sdk_project_and_experiment(map_client: MAPClient, project: dict):
    experiment = map_client.create_experiment(
        uuid.UUID(project["id"]),
        ExperimentCreate(
            title="SDK 实验",
            plan=PlanInput(content_md=make_valid_plan(body="## plan")),
            submit_for_review=True,
        ),
    )
    assert experiment.phase == ExperimentPhase.review

    detail = map_client.get_experiment(experiment.id)
    assert detail.current_plan_version == 1

    bundle = map_client.get_experiment_bundle(experiment.id)
    assert bundle.experiment.id == experiment.id
    assert len(bundle.plans) == 1


def test_sdk_experiment_acceptance_status_round_trip(map_client: MAPClient, project: dict):
    experiment = map_client.create_experiment(
        uuid.UUID(project["id"]),
        ExperimentCreate(
            title="SDK acceptance",
            plan=PlanInput(
                content_md=make_valid_plan(body="- [acceptance_type: smoke] map experiment status shows acceptance")
            ),
        ),
    )

    detail = map_client.get_experiment(experiment.id)

    assert len(detail.acceptance_status) == 1
    status = detail.acceptance_status[0]
    assert status.acceptance_type.value == "smoke"
    assert status.description == "map experiment status shows acceptance"
    assert status.evidence_provided is False


def test_sdk_full_lifecycle(map_client: MAPClient, client: TestClient, project: dict, admin_headers):
    reviewer = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={"name": "sdk-reviewer", "role": "agent", "project_key": project["project_key"]},
    ).json()
    reviewer_client = MAPClient(
        "http://test",
        reviewer["api_token"],
        transport=MAPTestClientTransport(client),
    )

    exp = map_client.create_experiment(
        uuid.UUID(project["id"]),
        ExperimentCreate(title="LC", plan=PlanInput(content_md=make_valid_plan(body="p")), submit_for_review=True),
    )

    review = reviewer_client.create_review(
        exp.id,
        ReviewCreate(reasonable_items=["ok"]),
    )
    assert review.items

    map_client.approve_experiment(exp.id)
    map_client.start_experiment(exp.id)

    submitted = map_client.complete_experiment(
        exp.id,
        ExperimentComplete(
            summary="done",
            content_md="result",
            metadata={"pytest_summary": "unit passed"},
        ),
    )
    assert submitted.phase == ExperimentPhase.result_review

    done = reviewer_client.accept_experiment_result(
        exp.id,
        ExperimentResultDecision(summary="accepted", content_md="result approved"),
    )
    assert done.phase == ExperimentPhase.done

    status = map_client.get_global_status()
    assert status.total_experiments_by_phase["done"] >= 1

    reviewer_client.close()


def test_sdk_http_error(map_client: MAPClient, admin_headers, client: TestClient):
    other = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={"project_key": "other-sdk", "name": "Other", "workspace_path": "/tmp/other"},
    ).json()
    with pytest.raises(MAPHTTPError) as exc:
        map_client.get_project(uuid.UUID(other["id"]))
    assert exc.value.status_code == 403


def test_sdk_topic_write_methods_return_410(
    map_client: MAPClient, db_session, project: dict
):
    """v0.13 M58: 话题域 SDK 写方法全链 410（含 body 序列化路径仍可达 server）。"""
    from server.domain.schemas import TopicCommentCreate, TopicCreate

    from tests._db_topic_factory import db_create_topic

    me = map_client.get_me()
    topic = db_create_topic(
        db_session,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=me.id,
        title="SDK 退役话题",
    )

    def expect_410(fn, label: str) -> None:
        with pytest.raises(MAPHTTPError) as exc:
            fn()
        assert exc.value.status_code == 410, (label, exc.value)
        assert "topic_write_retired" in str(exc.value), (label, exc.value)

    expect_410(
        lambda: map_client.create_topic(uuid.UUID(project["id"]), TopicCreate(title="SDK 话题")),
        "create_topic",
    )
    expect_410(
        lambda: map_client.create_topic_comment(topic.id, TopicCommentCreate(body="一条评论")),
        "create_topic_comment",
    )
    expect_410(
        lambda: map_client.create_topic_comment(
            topic.id, TopicCommentCreate(body="回复", parent_id=uuid.uuid4())
        ),
        "create_topic_comment(parent_id)",
    )
    expect_410(lambda: map_client.advance_topic_round(topic.id), "advance_topic_round")
    expect_410(lambda: map_client.close_topic(topic.id), "close_topic")
    expect_410(lambda: map_client.reopen_topic(topic.id), "reopen_topic")
    expect_410(
        lambda: map_client.resolve_topic(topic.id, TopicResolve(decision="d")),
        "resolve_topic",
    )


def test_sdk_experiment_with_topic_id(map_client: MAPClient, db_session, project: dict):
    """Regression: create_experiment with topic_id (UUID) must serialize the request body.

    v0.13 M58: 话题由 DB 直插创建（话题写端点已退役，实验域不受影响）。
    """
    from server.domain.schemas import ExperimentCreate, PlanInput

    from tests._db_topic_factory import db_create_topic

    me = map_client.get_me()
    topic = db_create_topic(
        db_session,
        project_id=uuid.UUID(project["id"]),
        creator_agent_id=me.id,
        title="实验源话题",
    )
    experiment = map_client.create_experiment(
        uuid.UUID(project["id"]),
        ExperimentCreate(title="带话题的实验", plan=PlanInput(content_md=make_valid_plan(body="p")), topic_id=topic.id),
    )
    assert experiment.topic_id == topic.id


def test_sdk_revise_plan_with_addressed_items(
    map_client: MAPClient, reviewer: dict, client: TestClient, project: dict
):
    """Regression: revise_plan with addressed_item_ids (list[UUID]) must serialize the request body."""
    from server.domain.schemas import PlanRevise, ReviewCreate

    exp = map_client.create_experiment(
        uuid.UUID(project["id"]),
        ExperimentCreate(title="争议实验", plan=PlanInput(content_md=make_valid_plan(body="p"))),
    )
    map_client.submit_for_review(exp.id)

    reviewer_client = MAPClient(
        "http://test",
        reviewer["headers"]["Authorization"].split(" ", 1)[1],
        transport=MAPTestClientTransport(client),
    )
    review = reviewer_client.create_review(exp.id, ReviewCreate(unreasonable_items=["这里有问题"]))
    item = next(i for i in review.items if i.kind.value == "unreasonable")

    version = map_client.revise_plan(
        exp.id, PlanRevise(content_md=make_valid_plan(body="## 修订"), addressed_item_ids=[item.id])
    )
    assert version.version == 2
    reviewer_client.close()


def test_sdk_notifications(map_client: MAPClient, client: TestClient, project: dict, admin_headers):
    reviewer = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={"name": "sdk-notify-reviewer", "role": "agent", "project_key": project["project_key"]},
    ).json()
    reviewer_client = MAPClient(
        "http://test",
        reviewer["api_token"],
        transport=MAPTestClientTransport(client),
    )

    map_client.create_experiment(
        uuid.UUID(project["id"]),
        ExperimentCreate(title="SDK notify", plan=PlanInput(content_md=make_valid_plan(body="p"))),
    )
    exp = map_client.list_experiments(uuid.UUID(project["id"]))[-1]
    map_client.submit_for_review(exp.id)

    inbox = reviewer_client.list_notifications()
    assert inbox.unread_count >= 1
    assert inbox.total >= 1
    notif = next(n for n in inbox.items if n.event == "experiment.phase_changed")
    assert notif.read_at is None
    assert notif.category.value == "digest"

    digest = reviewer_client.list_notifications(category="digest", unread_only=True)
    assert any(n.id == notif.id for n in digest.items)
    wakeable = reviewer_client.list_notifications(category="wakeable", unread_only=True)
    assert all(n.id != notif.id for n in wakeable.items)

    read = reviewer_client.mark_notification_read(notif.id)
    assert read.read_at is not None

    unread = reviewer_client.list_notifications(unread_only=True)
    assert all(n.read_at is not None for n in unread.items if n.id == notif.id)

    marked = reviewer_client.mark_all_notifications_read()
    assert marked["marked"] >= 0
    assert reviewer_client.list_notifications().unread_count == 0

    reviewer_client.close()


def test_sdk_list_experiments_page(map_client: MAPClient, project: dict):
    project_id = uuid.UUID(project["id"])
    map_client.create_experiment(
        project_id,
        ExperimentCreate(title="SDK page alpha", plan=PlanInput(content_md=make_valid_plan(body="p"))),
    )
    map_client.create_experiment(
        project_id,
        ExperimentCreate(title="SDK page beta", plan=PlanInput(content_md=make_valid_plan(body="p"))),
    )

    items, total = map_client.list_experiments_page(project_id, q="SDK page", page=1, page_size=1)
    assert total >= 2
    assert len(items) == 1
    assert items[0].title.startswith("SDK page")


# ---------------------------------------------------------------------------
# Experiment B / I7: SDK round-trip for action_item wake schema extensions
# ---------------------------------------------------------------------------


def test_sdk_action_item_wake_fields_round_trip(
    map_client: MAPClient, db_session, project: dict
):
    """I7: ``list_project_action_items`` round-trips the wake tracking fields
    added in I1/I4 (plan §7) so the CLI ``action list`` output and any SDK
    consumer can read them without going through the audit log.

    v0.13 M58: 话题 + decision/action_item 由 DB 直插创建（读路径消费者断言保留）。
    """
    from map_types.enums import TopicActionItemStatus

    from tests._db_topic_factory import db_create_topic, db_resolve_with_action_items

    project_id = uuid.UUID(project["id"])
    me = map_client.get_me()
    topic = db_create_topic(
        db_session,
        project_id=project_id,
        creator_agent_id=me.id,
        title="wake schema round-trip",
    )
    rows = db_resolve_with_action_items(
        db_session,
        topic,
        author=me,
        decision="schema round-trip",
        action_items=[{"title": "wake schema", "owner_agent_id": me.id}],
    )
    item_id = rows[0].id

    items = map_client.list_project_action_items(project_id, status=TopicActionItemStatus.open)
    matched = [i for i in items if i.id == item_id]
    assert len(matched) == 1, f"expected action_item in listing, got {items}"
    item = matched[0]
    for field in ("wake_count", "first_open_at", "last_woken_at", "stale_at"):
        assert hasattr(item, field), f"missing {field} on TopicActionItemRead"
    # Newly-created items default to wake_count=0 + null timestamps until I5 fires.
    assert item.wake_count == 0
    assert item.last_woken_at is None
    assert item.stale_at is None
    # first_open_at is stamped at creation time by I3 (per the I1 contract
    # that ``topic_service`` populates it on new rows).
    assert item.first_open_at is not None


def test_sdk_action_item_mark_wake_sent_round_trip(
    map_client: MAPClient, db_session, project: dict
):
    """I7: the I4 ``mark_wake_sent`` SDK method round-trips the wake_count
    bump + last_woken_at stamp back through ``TopicActionItemRead``.

    v0.13 M58: 话题 + decision/action_item 由 DB 直插创建（读路径消费者断言保留）。
    """
    from tests._db_topic_factory import db_create_topic, db_resolve_with_action_items

    project_id = uuid.UUID(project["id"])
    me = map_client.get_me()
    topic = db_create_topic(
        db_session,
        project_id=project_id,
        creator_agent_id=me.id,
        title="mark-wake-sent round-trip",
    )
    rows = db_resolve_with_action_items(
        db_session,
        topic,
        author=me,
        decision="mark-wake-sent round-trip",
        action_items=[{"title": "wake", "owner_agent_id": me.id}],
    )
    item_id = rows[0].id
    bumped = map_client.mark_wake_sent(item_id)
    assert bumped.wake_count == 1
    assert bumped.last_woken_at is not None
    assert bumped.stale_at is None
