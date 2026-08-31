## I1 — busy 状态机（migration + schema + endpoint）

### 改动文件

- `server/_migrate/alembic/versions/053_agent_last_busy_since.py` 新增 — revision=053, down=052, 幂等守卫 (`inspector.get_columns('agents')`)
- `server/domain/models.py` Agent 新增 `last_busy_since: Mapped[datetime | None]` (nullable TZ)
- `server/services/agent_heartbeat.py` 新增 — `set_last_busy_since()` 单列 UPDATE
- `sdk/python/map_types/schemas/agent.py` 新增 `AgentHeartbeatCreate` / `AgentHeartbeatResult`
- `sdk/python/map_types/schemas/__init__.py` `__all__` 注册两 schema
- `server/api/agents.py` 新增 `POST /api/v1/agents/me/heartbeat` handler

### 验证

- `ruff check` 全绿
- `alembic upgrade head` (在 `server/_migrate/` 下跑): 052 -> 053 成功，DB 出现 `agents.last_busy_since` 列
- Smoke test (TestClient):
  - set `busy_since`: 200，body echo 正确
  - clear (None): 200，DB 列被清零
  - empty body (`busy_since` 默认 None): 200，视为清零
- conftest import chain 恢复

### 设计要点 (A8 边界)

- 单列 UPDATE，与 D1 (`migration 050` `last_waker_poll_at`) 模式一致 — 不在 `get_current_agent` middleware 副作用里刷新
- 不动 `last_waker_poll_at` / `last_api_seen_at` — `busy_since` 由 waker state machine (I2) 独立控制刷新时机
- `busy_since=None` 视为 idle 清零（空 body 也按清零处理）

下一节: I2 — `cli/simple_waker.py` busy 状态机（remind 前 touch，finally 清零）
