from server.domain.models import AgentRole
from server.services.auth import TOKEN_PREFIX_LEN, create_agent, get_agent_by_token, hash_token, token_prefix


def test_token_prefix_length():
    assert TOKEN_PREFIX_LEN == 8


def test_create_agent_sets_token_prefix(db_session):
    agent, token = create_agent(db_session, "prefix-agent", AgentRole.admin)
    assert agent.api_token_prefix == token_prefix(token)
    assert len(agent.api_token_prefix) == TOKEN_PREFIX_LEN


def test_get_agent_by_token_uses_prefix(db_session):
    agent, token = create_agent(db_session, "lookup-agent", AgentRole.admin)
    found = get_agent_by_token(db_session, token)
    assert found is not None
    assert found.id == agent.id


def test_get_agent_by_token_rejects_wrong_token(db_session):
    create_agent(db_session, "secure-agent", AgentRole.admin)
    assert get_agent_by_token(db_session, "definitely-not-a-valid-token") is None


def test_get_agent_by_token_legacy_empty_prefix(db_session):
    """Agents migrated without prefix still authenticate via legacy scan."""
    from server.domain.models import Agent

    token = "legacy-test-token-value"
    agent = Agent(
        name="legacy-agent",
        api_token_hash=hash_token(token),
        api_token_prefix="",
        role=AgentRole.admin,
    )
    db_session.add(agent)
    db_session.commit()

    found = get_agent_by_token(db_session, token)
    assert found is not None
    assert found.id == agent.id


def test_bootstrap_first_admin(client):
    response = client.post("/api/v1/agents", params={"name": "bootstrap-admin", "role": "admin"})
    assert response.status_code == 201
    assert response.json()["role"] == "admin"
    assert "api_token" in response.json()


def test_bootstrap_rejects_non_admin(client):
    response = client.post("/api/v1/agents", params={"name": "bootstrap-agent", "role": "agent"})
    assert response.status_code == 403


def test_register_requires_auth_after_bootstrap(client, admin_headers):
    response = client.post(
        "/api/v1/agents",
        params={"name": "unauth-agent", "role": "agent", "project_key": "missing"},
    )
    assert response.status_code == 401


def test_register_requires_admin(client, auth_headers, project):
    response = client.post(
        "/api/v1/agents",
        headers=auth_headers,
        params={"name": "blocked-agent", "role": "agent", "project_key": project["project_key"]},
    )
    assert response.status_code == 403


def test_admin_can_register_agent(client, admin_headers, project):
    response = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        params={"name": "admin-created-agent", "role": "agent", "project_key": project["project_key"]},
    )
    assert response.status_code == 201
    assert response.json()["project_id"] == project["id"]
