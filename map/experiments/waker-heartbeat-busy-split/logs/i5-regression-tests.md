## I5 — 回归测试

### 改动文件

- `tests/test_simple_waker.py`：
  - `FakeMapClient.agent_heartbeat()` 加 noop 默认实现（避免既有 32 个测试因新代码走到 `_patch_server_busy` 而 fail）
  - 新增 `_RecordingHeartbeatClient`：记录 `agent_heartbeat` 调用
  - 新增 5 个 busy case：
    - `test_touch_busy_writes_state_and_patches_server`：state 三字段 + server PATCH
    - `test_clear_busy_purges_state_and_server`：同 PID clear
    - `test_clear_busy_cross_pid_preserves_state`：跨 PID 保留 state
    - `test_check_busy_crash_recovery_clears_dead_pid`：crash recovery
    - `test_run_once_calls_touch_and_clear_around_wake`：cycle 集成（touch → wake_async → clear）
- `tests/test_status_service.py`（新建）：
  - `probe_agent` fixture：fresh Agent,nested savepoint 自动回滚
  - 4 个 case 覆盖 build_waker_heartbeats busy/stale 分支：
    - `test_idle_old_poll_is_stale`：D1 既有语义保持
    - `test_idle_fresh_poll_is_not_stale`：D1 既有语义保持
    - `test_busy_recent_not_stale`：busy 5min + poll 2h → stale=False
    - `test_busy_exceeds_tolerance_is_stale`：busy 2h → stale=True（覆盖卡死盲区）
- `cli/map_command_client.py`：`_WRITE_COMMANDS_2` 加 `("agent", "heartbeat")` —— 修复 `test_every_subgroup_command_is_classified` 失败

### 验证

- `pytest tests/test_simple_waker.py tests/test_status_service.py tests/cli/test_dry_run_write_commands.py` 70 passed
- `pytest tests/ -q` 1772 passed, 0 failed（基线保持，9 个新加 busy case 全过）

下一节: I6 — commit + log + release（complete experiment）
