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


def test_sdk_project_and_experiment(map_client: MAPClient, project: dict):
    experiment = map_client.create_experiment(
        uuid.UUID(project["id"]),
        ExperimentCreate(
            title="SDK 实验",
            plan=PlanInput(content_md="## plan"),
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
                content_md="- [acceptance_type: smoke] map experiment status shows acceptance"
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
        params={"name": "sdk-reviewer", "role": "agent", "project_key": project["project_key"]},
    ).json()
    reviewer_client = MAPClient(
        "http://test",
        reviewer["api_token"],
        transport=MAPTestClientTransport(client),
    )

    exp = map_client.create_experiment(
        uuid.UUID(project["id"]),
        ExperimentCreate(title="LC", plan=PlanInput(content_md="p"), submit_for_review=True),
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


def test_sdk_topic_flow(map_client: MAPClient, project: dict):
    from server.domain.schemas import TopicCommentCreate, TopicCreate

    topic = map_client.create_topic(uuid.UUID(project["id"]), TopicCreate(title="SDK 话题"))
    assert topic.status.value == "open"
    assert topic.discussion_round.value == "round1"
    assert topic.round_summary_count == 0

    comment = map_client.create_topic_comment(topic.id, TopicCommentCreate(body="一条评论"))
    assert comment.body == "一条评论"

    advanced = map_client.advance_topic_round(topic.id)
    assert advanced.discussion_round.value == "round2"
    assert advanced.round_summary_count == 1

    detail = map_client.get_topic(topic.id)
    assert detail.comment_count == 1
    assert detail.discussion_round.value == "round2"
    assert detail.comments[0].body == "一条评论"

    closed = map_client.close_topic(topic.id)
    assert closed.status.value == "closed"

    reopened = map_client.reopen_topic(topic.id)
    assert reopened.status.value == "open"


def test_sdk_topic_comment_reply(map_client: MAPClient, project: dict):
    """Regression: create_topic_comment with parent_id (UUID) must serialize the request body."""
    from server.domain.schemas import TopicCommentCreate, TopicCreate

    topic = map_client.create_topic(uuid.UUID(project["id"]), TopicCreate(title="回复话题"))
    parent = map_client.create_topic_comment(topic.id, TopicCommentCreate(body="顶层"))
    reply = map_client.create_topic_comment(
        topic.id, TopicCommentCreate(body="回复", parent_id=parent.id)
    )
    assert reply.parent_comment_id == parent.id

    detail = map_client.get_topic(topic.id)
    assert detail.comment_count == 2
    assert detail.comments[0].children  # reply nested under its parent


def test_sdk_experiment_with_topic_id(map_client: MAPClient, project: dict):
    """Regression: create_experiment with topic_id (UUID) must serialize the request body."""
    from server.domain.schemas import ExperimentCreate, PlanInput, TopicCreate

    topic = map_client.create_topic(uuid.UUID(project["id"]), TopicCreate(title="实验源话题"))
    experiment = map_client.create_experiment(
        uuid.UUID(project["id"]),
        ExperimentCreate(title="带话题的实验", plan=PlanInput(content_md="p"), topic_id=topic.id),
    )
    assert experiment.topic_id == topic.id


def test_sdk_revise_plan_with_addressed_items(
    map_client: MAPClient, reviewer: dict, client: TestClient, project: dict
):
    """Regression: revise_plan with addressed_item_ids (list[UUID]) must serialize the request body."""
    from server.domain.schemas import PlanRevise, ReviewCreate

    exp = map_client.create_experiment(
        uuid.UUID(project["id"]),
        ExperimentCreate(title="争议实验", plan=PlanInput(content_md="p")),
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
        exp.id, PlanRevise(content_md="## 修订", addressed_item_ids=[item.id])
    )
    assert version.version == 2
    reviewer_client.close()


def test_sdk_notifications(map_client: MAPClient, client: TestClient, project: dict, admin_headers):
    reviewer = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        params={"name": "sdk-notify-reviewer", "role": "agent", "project_key": project["project_key"]},
    ).json()
    reviewer_client = MAPClient(
        "http://test",
        reviewer["api_token"],
        transport=MAPTestClientTransport(client),
    )

    map_client.create_experiment(
        uuid.UUID(project["id"]),
        ExperimentCreate(title="SDK notify", plan=PlanInput(content_md="p")),
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
        ExperimentCreate(title="SDK page alpha", plan=PlanInput(content_md="p")),
    )
    map_client.create_experiment(
        project_id,
        ExperimentCreate(title="SDK page beta", plan=PlanInput(content_md="p")),
    )

    items, total = map_client.list_experiments_page(project_id, q="SDK page", page=1, page_size=1)
    assert total >= 2
    assert len(items) == 1
    assert items[0].title.startswith("SDK page")


# ---------------------------------------------------------------------------
# Experiment B / I7: SDK round-trip for action_item wake schema extensions
# ---------------------------------------------------------------------------


def test_sdk_action_item_wake_fields_round_trip(
    map_client: MAPClient, project: dict
):
    """I7: ``list_project_action_items`` round-trips the wake tracking fields
    added in I1/I4 (plan §7) so the CLI ``action list`` output and any SDK
    consumer can read them without going through the audit log.

    Drives: create topic → resolve with action_item → ``list_project_action_items``
    → assert each wake field is present and parseable.
    """
    from map_types.enums import TopicActionItemStatus

    from server.domain.schemas import TopicCreate

    project_id = uuid.UUID(project["id"])
    me = map_client.get_me()
    topic = map_client.create_topic(project_id, TopicCreate(title="wake schema round-trip"))
    decision = map_client.resolve_topic(
        topic.id,
        TopicResolve(
            decision="schema round-trip",
            action_items=[TopicActionItemCreate(title="wake schema", owner_agent_id=me.id)],
        ),
    )
    items = map_client.list_project_action_items(project_id, status=TopicActionItemStatus.open)
    matched = [i for i in items if i.id == decision.action_items[0].id]
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
    map_client: MAPClient, project: dict
):
    """I7: the I4 ``mark_wake_sent`` SDK method round-trips the wake_count
    bump + last_woken_at stamp back through ``TopicActionItemRead``."""
    from server.domain.schemas import TopicCreate

    project_id = uuid.UUID(project["id"])
    me = map_client.get_me()
    topic = map_client.create_topic(project_id, TopicCreate(title="mark-wake-sent round-trip"))
    decision = map_client.resolve_topic(
        topic.id,
        TopicResolve(
            decision="mark-wake-sent round-trip",
            action_items=[TopicActionItemCreate(title="wake", owner_agent_id=me.id)],
        ),
    )
    item_id = decision.action_items[0].id
    bumped = map_client.mark_wake_sent(item_id)
    assert bumped.wake_count == 1
    assert bumped.last_woken_at is not None
    assert bumped.stale_at is None
