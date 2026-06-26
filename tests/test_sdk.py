import uuid

import pytest
from fastapi.testclient import TestClient

from map_client import MAPClient, MAPHTTPError
from map_client.testing import MAPTestClientTransport
from server.domain.models import ExperimentPhase
from server.domain.schemas import (
    ExperimentComplete,
    ExperimentCreate,
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

    done = map_client.complete_experiment(
        exp.id,
        ExperimentComplete(summary="done", content_md="result"),
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

    comment = map_client.create_topic_comment(topic.id, TopicCommentCreate(body="一条评论"))
    assert comment.body == "一条评论"

    detail = map_client.get_topic(topic.id)
    assert detail.comment_count == 1
    assert detail.comments[0].body == "一条评论"

    closed = map_client.close_topic(topic.id)
    assert closed.status.value == "closed"

    reopened = map_client.reopen_topic(topic.id)
    assert reopened.status.value == "open"


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
