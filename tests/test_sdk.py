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


def test_sdk_project_and_experiment(map_client: MAPClient):
    project = map_client.create_project("SDK 项目", "/tmp/sdk")
    assert project.name == "SDK 项目"

    experiment = map_client.create_experiment(
        project.id,
        ExperimentCreate(
            title="SDK 实验",
            plan=PlanInput(content_md="## plan"),
            submit_for_review=True,
        ),
    )
    assert experiment.phase == ExperimentPhase.review

    detail = map_client.get_experiment(experiment.id)
    assert detail.current_plan_version == 1


def test_sdk_full_lifecycle(map_client: MAPClient, client: TestClient):
    reviewer = client.post("/api/v1/agents", params={"name": "sdk-reviewer"}).json()
    reviewer_client = MAPClient(
        "http://test",
        reviewer["api_token"],
        transport=MAPTestClientTransport(client),
    )

    project = map_client.create_project("生命周期", "/tmp/lc")
    exp = map_client.create_experiment(
        project.id,
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


def test_sdk_http_error(map_client: MAPClient):
    with pytest.raises(MAPHTTPError) as exc:
        map_client.get_project(uuid.uuid4())
    assert exc.value.status_code == 404
