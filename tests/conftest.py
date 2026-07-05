import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from map_client import MAPClient
from map_client.testing import MAPTestClientTransport
from server.db.base import Base
from server.db.session import get_db
from server.main import create_app


# ---------------------------------------------------------------------------
# claude_cli marker：未登录时 skip 而非 fail
# ---------------------------------------------------------------------------
# 真调用 `claude --print` 的测试（waker phase2 e2e_a1b / a1_total 等）依赖
# claude CLI 登录态。CI 沙箱环境通常未登录，应 skip 而不是报 FAILED。
# 用法：在测试模块顶部 `pytestmark = pytest.mark.claude_cli`，本 fixture 自动
# 探活；若 claude 不可用或未登录则整模块 skip。
def _claude_cli_available() -> tuple[bool, str]:
    if not shutil.which("claude"):
        return False, "claude CLI not found in PATH"
    try:
        proc = subprocess.run(
            ["claude", "--print", "--dangerously-skip-permissions", "Reply with exactly one word: ok"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return False, "claude CLI timed out (>15s)"
    except Exception as exc:  # pragma: no cover - 防御性
        return False, f"claude CLI probe failed: {exc}"
    if proc.returncode != 0:
        return False, f"claude CLI rc={proc.returncode} (likely not logged in)"
    return True, "ok"


@pytest.fixture(autouse=True)
def skip_claude_cli_if_unavailable(request):
    """对标记了 `claude_cli` 的测试项，若 claude 不可用则 skip。"""
    marker = request.node.get_closest_marker("claude_cli")
    if marker is None:
        return
    ok, reason = _claude_cli_available()
    if not ok:
        pytest.skip(f"claude_cli unavailable: {reason}")


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


def _session_with_savepoints(connection) -> Session:
    """Return a Session whose commits only release nested savepoints."""
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess: Session, trans) -> None:
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    return session


@pytest.fixture
def db_session(engine):
    """Per-test session rolled back via an outer transaction + savepoints."""
    connection = engine.connect()
    transaction = connection.begin()
    session = _session_with_savepoints(connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


class _SameSessionLocal:
    """Route production ``SessionLocal()`` calls to the active test session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def __call__(self) -> Session:
        return self._session

    def __enter__(self) -> Session:
        return self._session

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


@pytest.fixture
def client(db_session):
    import server.main as main_module

    original_init_db = main_module.init_db
    main_module.init_db = lambda: None
    try:
        app = create_app()
    finally:
        main_module.init_db = original_init_db

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    import server.db.session as db_session_module

    original_session_local = db_session_module.SessionLocal
    db_session_module.SessionLocal = _SameSessionLocal(db_session)

    with TestClient(app) as test_client:
        yield test_client

    db_session_module.SessionLocal = original_session_local


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
def agent_token(client: TestClient, project: dict, admin_headers: dict[str, str]) -> tuple[str, str]:
    response = client.post(
        "/api/v1/agents",
        headers=admin_headers,
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
def reviewer(client: TestClient, project: dict, admin_headers: dict[str, str]) -> dict[str, str]:
    response = client.post(
        "/api/v1/agents",
        headers=admin_headers,
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
