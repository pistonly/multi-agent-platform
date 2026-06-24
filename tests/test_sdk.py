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


def test_sdk_full_lifecycle(map_client: MAPClient, client: TestClient, project: dict):
    reviewer = client.post(
        "/api/v1/agents",
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
