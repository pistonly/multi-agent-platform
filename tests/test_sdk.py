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
        ExperimentComplete(summary="done", content_md="result"),
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
