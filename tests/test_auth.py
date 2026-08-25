from server.domain.models import AgentRole
from server.services.auth import (
    LEGACY_CANDIDATE_LIMIT,
    TOKEN_PREFIX_LEN,
    create_agent,
    get_agent_by_token,
    hash_token,
    token_prefix,
    token_sha256,
)


def test_token_prefix_length():
    assert TOKEN_PREFIX_LEN == 8


def test_create_agent_sets_token_prefix(db_session):
    agent, token = create_agent(db_session, "prefix-agent", AgentRole.admin)
    assert agent.api_token_prefix == token_prefix(token)
    assert len(agent.api_token_prefix) == TOKEN_PREFIX_LEN


def test_create_agent_sets_token_sha256(db_session):
    """新签发 token 同时写入 sha256 快路径列（T01）。"""
    agent, token = create_agent(db_session, "sha256-agent", AgentRole.admin)
    assert agent.api_token_sha256 == token_sha256(token)
    assert len(agent.api_token_sha256) == 64


def test_get_agent_by_token_uses_prefix(db_session):
    agent, token = create_agent(db_session, "lookup-agent", AgentRole.admin)
    found = get_agent_by_token(db_session, token)
    assert found is not None
    assert found.id == agent.id


def test_get_agent_by_token_fast_path_avoids_bcrypt(db_session, monkeypatch):
    """sha256 命中时不得触发任何 bcrypt 校验（T01 核心收益）。"""
    agent, token = create_agent(db_session, "fast-path-agent", AgentRole.admin)

    def _fail(*args, **kwargs):  # pragma: no cover - 断言路径不应被走到
        raise AssertionError("fast path must not invoke bcrypt verify_token")

    monkeypatch.setattr("server.services.auth.verify_token", _fail)
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


def test_legacy_login_backfills_sha256(db_session, monkeypatch):
    """legacy agent 首次 bcrypt 登录后回填 sha256，二次登录走快路径（T01/T02）。"""
    from server.domain.models import Agent

    token = "legacy-backfill-token-value"
    agent = Agent(
        name="legacy-backfill-agent",
        api_token_hash=hash_token(token),
        api_token_prefix="",
        role=AgentRole.admin,
    )
    db_session.add(agent)
    db_session.commit()

    first = get_agent_by_token(db_session, token)
    assert first is not None
    assert first.api_token_sha256 == token_sha256(token)

    # 回填落库后，第二次登录不得再触发 bcrypt。
    def _fail(*args, **kwargs):  # pragma: no cover - 断言路径不应被走到
        raise AssertionError("backfilled agent must authenticate via sha256 lookup")

    monkeypatch.setattr("server.services.auth.verify_token", _fail)
    second = get_agent_by_token(db_session, token)
    assert second is not None
    assert second.id == first.id


def test_backfilled_agent_leaves_legacy_pool(db_session):
    """回填后的 agent 不再进入 legacy 候选集（T02 收敛）。"""
    from sqlalchemy import select

    from server.domain.models import Agent

    token = "legacy-pool-token-value"
    agent = Agent(
        name="legacy-pool-agent",
        api_token_hash=hash_token(token),
        api_token_prefix="",
        role=AgentRole.admin,
    )
    db_session.add(agent)
    db_session.commit()

    assert get_agent_by_token(db_session, token) is not None
    remaining = db_session.scalars(
        select(Agent).where(Agent.api_token_prefix == "", Agent.api_token_sha256.is_(None))
    ).all()
    assert remaining == []


def test_legacy_candidate_limit_is_bounded():
    """legacy 扫描必须有硬上限（防 CPU DoS 放大，T02）。"""
    assert 0 < LEGACY_CANDIDATE_LIMIT <= 512


def test_bootstrap_first_admin(client):
    response = client.post("/api/v1/agents", json={"name": "bootstrap-admin", "role": "admin"})
    assert response.status_code == 201
    assert response.json()["role"] == "admin"
    assert "api_token" in response.json()


def test_bootstrap_rejects_non_admin(client):
    response = client.post("/api/v1/agents", json={"name": "bootstrap-agent", "role": "agent"})
    assert response.status_code == 403


def test_register_requires_auth_after_bootstrap(client, admin_headers):
    response = client.post(
        "/api/v1/agents",
        json={"name": "unauth-agent", "role": "agent", "project_key": "missing"},
    )
    assert response.status_code == 401


def test_register_requires_admin(client, auth_headers, project):
    response = client.post(
        "/api/v1/agents",
        headers=auth_headers,
        json={"name": "blocked-agent", "role": "agent", "project_key": project["project_key"]},
    )
    assert response.status_code == 403


def test_admin_can_register_agent(client, admin_headers, project):
    response = client.post(
        "/api/v1/agents",
        headers=admin_headers,
        json={"name": "admin-created-agent", "role": "agent", "project_key": project["project_key"]},
    )
    assert response.status_code == 201
    assert response.json()["project_id"] == project["id"]
