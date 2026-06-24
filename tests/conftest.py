import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from map_client import MAPClient
from map_client.testing import MAPTestClientTransport
from server.db.base import Base
from server.db.session import get_db
from server.main import create_app


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session):
    app = create_app()

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def admin_token(client: TestClient) -> tuple[str, str]:
    response = client.post("/api/v1/agents", params={"name": "admin-agent", "role": "admin"})
    assert response.status_code == 201
    data = response.json()
    return data["id"], data["api_token"]


@pytest.fixture
def admin_headers(admin_token: tuple[str, str]) -> dict[str, str]:
    _, token = admin_token
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def project(client: TestClient, admin_headers: dict[str, str]) -> dict:
    response = client.post(
        "/api/v1/projects",
        headers=admin_headers,
        json={
            "project_key": "test-project",
            "name": "Test Project",
            "workspace_path": "/tmp/test-project",
            "description": "测试项目",
        },
    )
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def agent_token(client: TestClient, project: dict) -> tuple[str, str]:
    response = client.post(
        "/api/v1/agents",
        params={"name": "test-agent", "role": "agent", "project_key": project["project_key"]},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["project_id"] == project["id"]
    return data["id"], data["api_token"]


@pytest.fixture
def auth_headers(agent_token: tuple[str, str]) -> dict[str, str]:
    _, token = agent_token
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def reviewer(client: TestClient, project: dict) -> dict[str, str]:
    response = client.post(
        "/api/v1/agents",
        params={"name": "reviewer-agent", "role": "agent", "project_key": project["project_key"]},
    )
    assert response.status_code == 201
    data = response.json()
    return {
        "id": data["id"],
        "headers": {"Authorization": f"Bearer {data['api_token']}"},
    }


@pytest.fixture
def map_client(client: TestClient, agent_token: tuple[str, str]) -> MAPClient:
    _, token = agent_token
    c = MAPClient("http://test", token, transport=MAPTestClientTransport(client))
    yield c
    c.close()


@pytest.fixture
def admin_map_client(client: TestClient, admin_token: tuple[str, str]) -> MAPClient:
    _, token = admin_token
    c = MAPClient("http://test", token, transport=MAPTestClientTransport(client))
    yield c
    c.close()
