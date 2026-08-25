"""A2A 互操作投影端点测试（M53）。

覆盖：
- Agent Card：项目级 / 单 agent、字段结构（name/description/skills/capabilities）
- 鉴权边界：无 token 401、跨项目 403、不存在 404（评审建议 1 落实）
- Task 投影：experiment phase → A2A task state 与 a2a_mapping 常量一致
- 文档护栏：docs/A2A-MAPPING.md 与 server/domain/a2a_mapping.py 同源不漂移
"""

from __future__ import annotations

from pathlib import Path

from server.domain.a2a_mapping import (
    A2A_PROTOCOL_BASELINE,
    A2A_TASK_STATES,
    EXPERIMENT_TASK_STATE,
    PERSONA_CARDS,
    TOPIC_TASK_STATE,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _bootstrap(
    client, key: str = "a2a-demo", workspace: str = "/tmp/a2a-demo"
) -> dict:
    resp = client.post(
        "/api/v1/bootstrap",
        json={
            "project_key": key,
            "project_name": "A2A Demo",
            "workspace_path": workspace,
        },
    )
    assert resp.status_code == 201
    return resp.json()


def _persona(body: dict, persona: str) -> dict:
    return next(a for a in body["agents"] if a["persona"] == persona)


def _headers(agent: dict) -> dict:
    return {"Authorization": f"Bearer {agent['api_token']}"}


# ---------------------------------------------------------------------------
# Agent Card
# ---------------------------------------------------------------------------


class TestAgentCards:
    def test_project_cards_cover_three_personas(self, client):
        body = _bootstrap(client)
        project_id = body["project"]["id"]
        host = _persona(body, "host")

        resp = client.get(
            f"/api/v1/projects/{project_id}/agent-cards",
            headers=_headers(host),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["protocol"] == A2A_PROTOCOL_BASELINE

        by_name = {c["name"]: c for c in data["items"]}
        host_card = by_name["a2a-demo-host"]
        assert host_card["description"]
        assert {s["id"] for s in host_card["skills"]} >= {"topic-host", "map-project-collab"}
        assert "experiment.create" in host_card["capabilities"]
        assert "experiment.review" in by_name["a2a-demo-reviewer"]["capabilities"]
        assert "topic.comment" in by_name["a2a-demo-participant"]["capabilities"]

    def test_single_agent_card(self, client):
        body = _bootstrap(client)
        participant = _persona(body, "participant")

        resp = client.get(
            f"/api/v1/agents/{participant['agent_id']}/agent-card",
            headers=_headers(participant),
        )
        assert resp.status_code == 200
        card = resp.json()
        assert card["name"] == "a2a-demo-participant"
        assert card["id"] == participant["agent_id"]
        assert all({"id", "name"} <= set(s) for s in card["skills"])

    def test_cards_require_auth(self, client):
        body = _bootstrap(client)
        project_id = body["project"]["id"]
        resp = client.get(f"/api/v1/projects/{project_id}/agent-cards")
        assert resp.status_code == 401

    def test_card_cross_project_forbidden(self, client):
        # 两个 project 用不同 workspace：A5 联合键唯一性下 workspace 不可复用
        first = _bootstrap(client, "a2a-one", "/tmp/a2a-one")
        second = _bootstrap(client, "a2a-two", "/tmp/a2a-two")
        host_two = _persona(second, "host")

        resp = client.get(
            f"/api/v1/agents/{_persona(first, 'host')['agent_id']}/agent-card",
            headers=_headers(host_two),
        )
        assert resp.status_code == 403

    def test_card_unknown_agent_404(self, client):
        body = _bootstrap(client)
        host = _persona(body, "host")
        resp = client.get(
            "/api/v1/agents/00000000-0000-0000-0000-000000000000/agent-card",
            headers=_headers(host),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Task 投影
# ---------------------------------------------------------------------------


class TestTaskProjection:
    def test_experiment_task_state_matches_mapping(self, client):
        body = _bootstrap(client)
        host = _persona(body, "host")
        project_id = body["project"]["id"]

        created = client.post(
            f"/api/v1/projects/{project_id}/experiments",
            headers=_headers(host),
            json={
                "title": "a2a probe",
                "description": "check task projection",
                "plan": {
                    "content_md": (
                        "---\ntitle: a2a probe\nacceptance:\n  - 'task state matches mapping'\nevidence_keys:\n  - 'pytest'\ndependencies:\n  - none\n---\n\n# probe\n"
                    )
                },
            },
        )
        assert created.status_code == 201
        experiment_id = created.json()["id"]

        resp = client.get(
            f"/api/v1/agents/{host['agent_id']}/tasks",
            headers=_headers(host),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["protocol"] == A2A_PROTOCOL_BASELINE

        exp_tasks = [t for t in data["tasks"] if t["kind"] == "experiment"]
        assert any(
            t["id"] == experiment_id and t["status"] == "submitted" for t in exp_tasks
        )

    def test_projection_only_emits_known_states(self, client):
        body = _bootstrap(client)
        host = _persona(body, "host")
        resp = client.get(
            f"/api/v1/agents/{host['agent_id']}/tasks",
            headers=_headers(host),
        )
        assert resp.status_code == 200
        for task in resp.json()["tasks"]:
            assert task["status"] in A2A_TASK_STATES

    def test_tasks_cross_project_forbidden(self, client):
        # 两个 project 用不同 workspace：A5 联合键唯一性下 workspace 不可复用
        first = _bootstrap(client, "a2a-one", "/tmp/a2a-one")
        second = _bootstrap(client, "a2a-two", "/tmp/a2a-two")
        resp = client.get(
            f"/api/v1/agents/{_persona(first, 'host')['agent_id']}/tasks",
            headers=_headers(_persona(second, "host")),
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 映射表完整性与文档护栏
# ---------------------------------------------------------------------------


class TestMappingContract:
    def test_every_experiment_phase_mapped(self):
        from map_types.enums import ExperimentPhase

        assert set(EXPERIMENT_TASK_STATE) == set(ExperimentPhase)

    def test_every_persona_has_card(self):
        assert set(PERSONA_CARDS) == {"host", "participant", "reviewer"}

    def test_docs_mapping_table_in_sync(self):
        doc = (REPO_ROOT / "docs" / "A2A-MAPPING.md").read_text(encoding="utf-8")
        assert A2A_PROTOCOL_BASELINE.split(" (")[0] in doc
        # experiment 映射行逐条出现在文档表格中

        for phase, state in EXPERIMENT_TASK_STATE.items():
            assert f"{phase.value}" in doc, f"doc missing phase {phase.value}"
            assert state in doc, f"doc missing state {state}"
        for topic_status, state in TOPIC_TASK_STATE.items():
            assert topic_status.value in doc
            assert state in doc
