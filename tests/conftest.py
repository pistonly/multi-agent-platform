import os
import re
import shutil
import subprocess
from pathlib import Path

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
# claude CLI 凭证。凭证解析顺序与 scripts/claude-{host,participant,reviewer}-runner.py
# 保持一致：
#   1. 已存在的环境变量（ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL / ANTHROPIC_MODEL）
#   2. 项目根目录 .map/.claude-env（export VAR=... 格式）
#   3. ~/.bashrc / ~/.profile / ~/.bash_profile
# 用法：在测试模块顶部 `pytestmark = pytest.mark.claude_cli`，本 fixture 自动
# 探活；若 claude 不可用或凭证缺失则 skip 而非 fail。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CLAUDE_ENV_FILE = PROJECT_ROOT / ".map" / ".claude-env"
_EXPORT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _read_export_from_file(path: Path, name: str) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        match = _EXPORT_RE.match(line)
        if match and match.group(1) == name:
            value = match.group(2).strip().strip("\"'").strip()
            if value:
                return value
    return None


def _resolve_claude_env_var(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    home = Path.home()
    for path in (_CLAUDE_ENV_FILE, home / ".bashrc", home / ".profile", home / ".bash_profile"):
        found = _read_export_from_file(path, name)
        if found:
            return found
    return None


def _build_claude_env() -> dict[str, str]:
    """构造 claude CLI 子进程环境：继承当前 env + 解析 .map/.claude-env。"""
    env = os.environ.copy()
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL"):
        resolved = _resolve_claude_env_var(name)
        if resolved:
            env[name] = resolved
    return env


def _claude_cli_available() -> tuple[bool, str]:
    if not shutil.which("claude"):
        return False, "claude CLI not found in PATH"
    env = _build_claude_env()
    has_token = bool(env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN"))
    if not has_token:
        return False, "no ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN (env + .map/.claude-env + shell rc)"
    try:
        proc = subprocess.run(
            ["claude", "--print", "--dangerously-skip-permissions", "Reply with exactly one word: ok"],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, "claude CLI timed out (>20s)"
    except Exception as exc:  # pragma: no cover - 防御性
        return False, f"claude CLI probe failed: {exc}"
    if proc.returncode != 0:
        snippet = (proc.stdout or proc.stderr or "").strip().splitlines()[:1]
        return False, f"claude CLI rc={proc.returncode} ({snippet[0] if snippet else 'no output'})"
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


@pytest.fixture
def claude_cli_env() -> dict[str, str]:
    """供 claude_cli 测试使用的子进程环境（已合并 .map/.claude-env）。"""
    return _build_claude_env()


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
    # ``init_db_on_startup=False`` 跳过 lifespan 中的 ``init_db()`` 调用，
    # 改由 ``engine`` fixture 在会话级 ``Base.metadata.create_all`` 完成；
    # 之前对 ``server.main.init_db`` 的 monkey-patch 不再需要。
    app = create_app(init_db_on_startup=False)

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
