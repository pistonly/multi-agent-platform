## I4 — 渲染 busy 字段（cli/waker_heartbeat_render.py）

### 改动文件

- `cli/waker_heartbeat_render.py` `render_waker_heartbeat_banner`：
  - 新增 `last_busy_since` 字段渲染（无值不显示）
  - busy 行不输出 idle stale WARN（busy 期间 polling 暂停属正常）
  - busy > 2h 输出软警告 `[WARN] busy > 2h`
  - busy 软警告对应 HINT：提示 waker / runtime 子进程排查 + 重启后 `_check_busy_crash_recovery` 会清 server 列

### 设计要点 (A5)

- 软警告阈值 2h 是运维判断点（与 server busy_tolerance 30min 不同）：
  - server 30min 判活：决定 stale 字段（避免误报 stale）
  - CLI 2h 软警告：覆盖「卡死但 polling heartbeat 仍新」的盲区（提醒运维介入）
- busy 行的 `state=busy` 永远显示，无论 stale 与否
- `last_busy_since` 字段格式与 `last_waker_poll_at` 一致（ISO-8601 UTC）

### 验证

- `ruff check` 全绿
- e2e (FakeRow + MagicMock) 4 个 case：
  - Case 1（idle stale）：输出 stale WARN + HINT ✓
  - Case 2（busy 5min）：state=busy，无 WARN ✓
  - Case 3（busy 3h）：输出 `[WARN] busy > 2h` + HINT ✓
  - Case 4（busy stale 1h, server 标记 stale）：busy 行**不输出** idle stale WARN ✓

下一节: I5 — 回归测试 `tests/test_simple_waker.py` busy case + `tests/test_status_service.py` busy/stale 分支
