import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def approved_experiment(client: TestClient, auth_headers: dict[str, str], reviewer: dict, project: dict) -> dict:
    experiment = client.post(
        f"/api/v1/projects/{project['id']}/experiments",
        headers=auth_headers,
        json={
            "title": "执行实验",
            "plan": {"content_md": "## 计划"},
            "submit_for_review": True,
        },
    ).json()
    exp_id = experiment["id"]

    review = client.post(
        f"/api/v1/experiments/{exp_id}/reviews",
        headers=reviewer["headers"],
        json={"reasonable_items": ["OK"]},
    ).json()
    for item in review["items"]:
        if item["kind"] == "unreasonable":
            client.patch(
                f"/api/v1/review-items/{item['id']}",
                headers=reviewer["headers"],
                json={"status": "resolved"},
            )

    client.post(f"/api/v1/experiments/{exp_id}/approve", headers=auth_headers)
    return {"project_id": project["id"], "experiment_id": exp_id}


def test_start_complete_result_review_accept_flow(client, auth_headers, reviewer, approved_experiment):
    exp_id = approved_experiment["experiment_id"]

    started = client.post(f"/api/v1/experiments/{exp_id}/start", headers=auth_headers)
    assert started.status_code == 200
    assert started.json()["phase"] == "running"

    interim_log = client.post(
        f"/api/v1/experiments/{exp_id}/logs",
        headers=auth_headers,
        json={"summary": "进度 50%", "content_md": "进行中...", "metadata": {"progress": 0.5}},
    )
    assert interim_log.status_code == 201
    assert interim_log.json()["metadata_json"]["progress"] == 0.5

    completed = client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=auth_headers,
        json={
            "summary": "实验完成",
            "content_md": "## 结果\n基线噪声 0.02",
            "metadata": {"metric": 0.02},
        },
    )
    assert completed.status_code == 200
    assert completed.json()["phase"] == "result_review"

    self_accept = client.post(
        f"/api/v1/experiments/{exp_id}/accept-result",
        headers=auth_headers,
        json={"summary": "自审", "content_md": "不允许"},
    )
    assert self_accept.status_code == 403

    accepted = client.post(
        f"/api/v1/experiments/{exp_id}/accept-result",
        headers=reviewer["headers"],
        json={
            "summary": "结果审批通过",
            "content_md": "结果满足计划验收标准",
            "metadata": {"approved": True},
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["phase"] == "done"

    logs = client.get(f"/api/v1/experiments/{exp_id}/logs", headers=auth_headers)
    assert logs.status_code == 200
    assert len(logs.json()) == 3

    detail = client.get(f"/api/v1/experiments/{exp_id}", headers=auth_headers).json()
    assert detail["log_count"] == 3
    assert detail["latest_log_summary"] == "结果审批通过"

    extra = client.post(
        f"/api/v1/experiments/{exp_id}/logs",
        headers=auth_headers,
        json={"summary": "补充说明", "content_md": "追加记录"},
    )
    assert extra.status_code == 201


def test_reject_result_returns_to_running(client, auth_headers, reviewer, approved_experiment):
    exp_id = approved_experiment["experiment_id"]

    client.post(f"/api/v1/experiments/{exp_id}/start", headers=auth_headers)
    submitted = client.post(
        f"/api/v1/experiments/{exp_id}/complete",
        headers=auth_headers,
        json={"summary": "实验完成", "content_md": "结果需要检查"},
    )
    assert submitted.status_code == 200
    assert submitted.json()["phase"] == "result_review"

    rejected = client.post(
        f"/api/v1/experiments/{exp_id}/reject-result",
        headers=reviewer["headers"],
        json={"summary": "结果驳回", "content_md": "缺少关键验收证据"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["phase"] == "running"

    logs = client.get(f"/api/v1/experiments/{exp_id}/logs", headers=auth_headers)
    assert [log["summary"] for log in logs.json()][-1] == "结果驳回"


def test_cannot_log_before_running(client, auth_headers, approved_experiment):
    exp_id = approved_experiment["experiment_id"]
    response = client.post(
        f"/api/v1/experiments/{exp_id}/logs",
        headers=auth_headers,
        json={"summary": "too early", "content_md": "x"},
    )
    assert response.status_code == 422


def test_global_status(client, auth_headers, approved_experiment):
    response = client.get("/api/v1/status", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "total_experiments_by_phase" in body
    assert body["total_experiments_by_phase"]["approved"] >= 1
    assert len(body["projects"]) >= 1

    filtered = client.get(
        f"/api/v1/status?project_id={approved_experiment['project_id']}",
        headers=auth_headers,
    )
    assert filtered.status_code == 200
    assert len(filtered.json()["projects"]) == 1
