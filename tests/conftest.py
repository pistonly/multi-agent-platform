import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from map_client import MAPClient
from map_client.testing import MAPTestClientTransport
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

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


# PR3 fast gate：仅下列模块参与默认 `pytest`（见 tests/README.md）；其余未标 integration/claude_cli 的用例默认视为 slow。
_FAST_GATE_MODULES = frozenset(
    {
        "test_agent_client",
        "test_bridge_state",
        "test_experiment_lock_unit",
        "test_git_checkpoint",
        "test_mcp_http",
        "test_runtime_chat",
        "test_sdk_exceptions",
        "test_sse_isolation",
        "test_sse_schema_overlap",
        "test_dry_run_write_commands",
        "test_similarity_service",
        "test_similarity_api_contract",
        "test_plan_marker_service",
        "test_archive_lint_service",
        "test_plan_validate_cli",
        # eng experiment (55634575) PR6 — typing_extensions 守卫
        "test_eng_typing_extensions_clean",
        # eng experiment (55634575) PR7 — server/__version__ 单一源
        "test_eng_version_single_source",
        # eng experiment (55634575) PR1 — uv.lock 存在 + 入库 + lock --check
        "test_eng_uv_lock",
        # eng experiment (55634575) PR3-PR5j — mypy --strict 14 模块守卫
        # 这些模块跑 `mypy --strict <file>` subprocess,~25ms/case,加进
        # fast gate 才能真正 verify strict promotion 没回归(否则 silently
        # deselected)。
        "test_eng_mypy_strict_topics",
        "test_eng_mypy_strict_experiments_projects",
        "test_eng_mypy_strict_agents",
        "test_eng_mypy_strict_project_service",
        "test_eng_mypy_strict_phase_service",
        "test_eng_mypy_strict_experiment_capabilities_service",
        "test_eng_mypy_strict_mention_service",
        "test_eng_mypy_strict_todo_service",
        "test_eng_mypy_strict_topic_comment_service",
        "test_eng_mypy_strict_agent_work_service",
        "test_eng_mypy_strict_topic_work_item_service",
        "test_eng_mypy_strict_notification_service",
        "test_eng_mypy_strict_topic_service",
        # eng experiment (55634575) PR9 — 白名单自身一致性守卫
        # (测试模块名集合 == _FAST_GATE_MODULES 集合)
        "test_eng_fast_gate_whitelist_complete",
        # arch experiment (0519e2a3) PR8 — CLI surface snapshot 守卫
        # 用 CliRunner 走 --help,无 API / 无 subprocess,~25ms 全量。
        # 加 fast gate 才真能拦下 cli/main.py 偷偷涨行或 sub-app 漏注册。
        "test_compat",
        # cleanup follow-up (c9281d86) PR1 — phase_owner ORM @validates 守卫
        # ORM-level test,~50ms 全量;默认 pytest 必须跑才能守住
        # silent-drift 防护。
        "test_phase_owner_orm_validation",
        # cleanup follow-up (c9281d86) PR3 — webhook secret Fernet 加密
        # ORM + 加密 helper test,~100ms 全量;默认 pytest 必须跑才能守住
        # 「DB 不落明文」契约。
        "test_webhook_secret_encryption",
        # cleanup follow-up (c9281d86) PR2 — docs/ 收纳结构守卫
        # 纯文件系统 glob,~10ms 全量;默认 pytest 守住「PRD 不能逃出
        # docs/prd/」契约。
        "test_docs_inventory",
        # PRD v0.11 M50 — 文档内容一致性守卫（参数/端口/现行版本/Skill 路径）
        # 纯文件读取与正则,~50ms 全量;默认 pytest 守住文档漂移护栏。
        "test_docs_consistency",
        # cli-ux follow-up (1f9c9a50) PR1 — --schema flag + error schema 提示守卫
        # subprocess 跑 CLI,~150ms 全量;默认 pytest 守住「schema 发现」契约。
        "test_cli_schema_discovery",
        # cli-ux follow-up (1f9c9a50) PR2 — notification read-all --category / --event
        # CliRunner 跑 typer app,~100ms 全量;守住「bulk filter 走 enumerate 路径 /
        # 无 filter 走单次 bulk endpoint」契约。
        "test_notification_bulk_filter",
        # cli-ux follow-up (1f9c9a50) PR3 — verdict vocabulary alias
        # 纯 Pydantic 模型测试,~30ms 全量;守住「alias 不绕过 waived 必填 reason」契约。
        "test_review_verdict_alias",
        # self-service bootstrap endpoint — POST /api/v1/bootstrap 无 admin token
        # 创建 project + 3 persona agents;~3s 全量,守住「原子性 + 409 冲突」契约。
        "test_bootstrap",
        # fs plane — map/ 文件夹事实源（解析器 + API 合并 + 验证型写），
        # 纯 tmp_path 文件系统 + TestClient，秒级，默认 gate 必须跑。
        "test_fs_source",
        # fs CLI persona 解析 — 全局 map --persona 透传到 fs 子命令，
        # CliRunner 进程内跑，~100ms，守住「Agent 不需重复传 --persona」契约。
        "test_fs_persona",
        # PRD v0.11 M52 — Skill 分发可靠性（map-plugin.yaml 版本化 +
        # --runtime 多目标 + upgrade diff 摘要），CliRunner 进程内跑，
        # ~200ms 全量；守住「无静默跳过 + 版本漂移可见」契约。
        "test_skill_versioning",
        # PRD v0.11 M52C — map auth reissue SDK 回写路径，
        # httpx.MockTransport 纯内存单测，~50ms；守住裸 404 友好降级
        # 与 agents.local.yaml 精确 persona 回写契约。
        "test_auth_reissue",
        # PRD v0.11 M51 — map topic --id 统一路由（DB/FS）+ migrate
        # 单向迁移。stub client + tmp workspace 纯本地，~200ms；
        # 守住「uuid DB 优先 / slug FS 优先 / --storage 覆盖」与
        # 「FS 完整落盘后才 archive」契约。
        "test_topic_routing",
        "test_topic_migrate",
        # PRD v0.11 M53 — A2A 只读投影（Agent Card + Task 状态映射），
        # TestClient + bootstrap fixture，~2s；守住「卡片字段最小对 +
        # 鉴权 403/404 + 映射表与文档同源」契约。
        "test_a2a",
    }
)


def pytest_collection_modifyitems(config, items):
    for item in items:
        if (
            item.get_closest_marker("slow")
            or item.get_closest_marker("integration")
            or item.get_closest_marker("claude_cli")
        ):
            continue
        mod = item.module.__name__.rsplit(".", 1)[-1]
        if mod not in _FAST_GATE_MODULES:
            item.add_marker(pytest.mark.slow)


@pytest.fixture(autouse=True)
def skip_claude_cli_if_unavailable(request):
    """对标记了 `claude_cli` 的测试项，若 claude 不可用则 skip。"""
    marker = request.node.get_closest_marker("claude_cli")
    if marker is None:
        return
    ok, reason = _claude_cli_available()
    if not ok:
        pytest.skip(f"claude_cli unavailable: {reason}")


def _claude_runtime_available() -> tuple[bool, str]:
    """Probe whether claude_agent_sdk is importable."""
    import importlib.util

    spec = importlib.util.find_spec("claude_agent_sdk")
    if spec is None:
        return False, "claude_agent_sdk not installed (pip install -e '.[dev,claude-runtime]')"
    return True, "ok"


@pytest.fixture(autouse=True)
def skip_claude_runtime_if_unavailable(request):
    """对标记了 `claude_runtime` 的测试项，若 SDK 不可 import 则 skip。

    与 `skip_claude_cli_if_unavailable` 对称：后者探活 claude CLI 子进程，
    本 fixture 探活 claude_agent_sdk Python 包。模块级 `pytest.importorskip`
    已经在 collection 期兜底，本 fixture 提供更细粒度的运行时门禁。
    """
    marker = request.node.get_closest_marker("claude_runtime")
    if marker is None:
        return
    ok, reason = _claude_runtime_available()
    if not ok:
        pytest.skip(f"claude_runtime unavailable: {reason}")


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
    response = client.post("/api/v1/agents", json={"name": "admin-agent", "role": "admin"})
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
        json={"name": "test-agent", "role": "agent", "project_key": project["project_key"]},
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
        json={"name": "reviewer-agent", "role": "agent", "project_key": project["project_key"]},
    )
    assert response.status_code == 201
    data = response.json()
    return {
        "id": data["id"],
        "headers": {"Authorization": f"Bearer {data['api_token']}"},
    }


# c9281d86 PR3 — deterministic Fernet key for tests that touch webhook
# secrets. Any test (existing or new) that writes a ``Webhook.secret`` row
# needs a resolvable key; otherwise ``SecretEncryptionKeyMissing`` fires
# at INSERT time. We inject a fixed session-scoped env var so all tests
# share one key path; tests that explicitly rotate (via
# ``server.services.secret_encryption.set_key_for_tests``) still work.
_FERNET_TEST_KEY = "WaU0vwQr9VMUlM7F7VZ1_LbVSvsfYo1xDKNhhQ7z8sc="


@pytest.fixture(autouse=True, scope="session")
def _webhook_secret_encryption_key_for_tests():
    os.environ.setdefault("MAP_WEBHOOK_SECRET_ENCRYPTION_KEY", _FERNET_TEST_KEY)
    # Clear the lru_cache so pydantic-settings re-reads the env var.
    from server.config import get_settings

    get_settings.cache_clear()
    yield
    # Don't unset — subsequent tests in the session still need it.


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
