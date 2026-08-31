## I2 — waker busy 状态机（cli/simple_waker.py）

### 改动文件

- `sdk/python/map_client/client.py` 新增 `MAPClient.agent_heartbeat(payload)` — POST /api/v1/agents/me/heartbeat
- `sdk/python/map_types/__init__.py` 顶层 export `AgentHeartbeatCreate` / `AgentHeartbeatResult`
- `cli/map_sdk_client.py` 新增 `MapSdkClient.agent_heartbeat(*, busy_since)` — dry-run 兼容 + 错误转 WorkerError
- `cli/map_command_client.py` 新增 `MapCommandClient.agent_heartbeat(*, busy_since)` — `map agent heartbeat --busy-since|--clear` 子进程路径
- `cli/commands/agent.py` 新增 `agent heartbeat` CLI 命令（包装 `AgentHeartbeatCreate` payload + ISO-8601 解析 + 互斥校验）
- `cli/simple_waker.py`：
  - `SimpleWaker.__init__` 末尾调用 `_check_busy_crash_recovery()`（启动期回收）
  - `_run_once_async` 在 `wake_async` 前调用 `_touch_busy`，`finally` 调用 `_clear_busy`
  - 新增 `_touch_busy` / `_clear_busy` / `_check_busy_crash_recovery` / `_patch_server_busy` 四方法
  - state 文件 `personas.<persona>` 写入 `session_busy_since` / `busy_pid` / `busy_started_at` 三个字段

### 验证

- `ruff check` 全绿（5 个改动文件）
- e2e 脚本（in-process SimpleWaker + FakeClient）：
  - `_touch_busy` → state 含三字段 + server PATCH `busy_since=now`
  - `_clear_busy` 同 PID → state 三字段全清 + server PATCH `busy_since=None`
  - `_clear_busy` 跨 PID（busy_pid=99999）→ state 保留 + server PATCH clear（避免误清别的进程记录）
  - `_check_busy_crash_recovery` 死 PID → state 清空 + server PATCH clear
- CLI e2e:
  - `map agent heartbeat --busy-since "2026-08-31T11:00:00+00:00"` → 200 + echo
  - `map agent heartbeat --clear` → 200 + busy_since:null
- server_daemon 重启后 `curl POST /api/v1/agents/me/heartbeat` 200

### 设计要点 (A8 边界)

- PATCH server 失败兜底 `WorkerError` → 不阻塞 remind（心跳信号 best-effort）
- PID 自检防止跨进程误清 state
- crash recovery 在 `__init__` 末尾跑一次，启动期就把上轮残留回收
- 没引入新 wake signature / work_items kind（与 83bf610 + 8b1d20a1 fan-out 互不干扰）

下一节: I3 — `server/services/status_service.py` busy/stale 分支计算 + `WakerHeartbeatRead.last_busy_since`
