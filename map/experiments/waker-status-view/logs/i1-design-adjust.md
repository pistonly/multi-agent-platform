# I1 — 设计收敛：复用既有 simple-waker-state 文件

## 调研发现

- `.map/simple-waker-state-host.json` 已 53KB（persona 运行时 state，含 `busy_pid` / `busy_started_at` / `claude_session_id` / `runtime_session_id` / `last_cycle_error` / `last_cycle_error_at` / `runtime_contract_hash` / `runtime_backend` 等）
- `.map/simple-waker-state-participant.json` 0.9KB
- `.map/waker-health.state` 存在但只有占位 "0"
- `.map/runtime-waker-state-{persona}.json` 是 legacy runtime-waker 状态，与 simple-waker 无重叠

## 字段冲突分析

| plan 中字段 | 已有/新增 | 复用方式 |
|------------|----------|---------|
| pid | 已有 `busy_pid` | 直接复用 |
| uptime | 新增 | 派生自 `busy_started_at` |
| last_poll | 新增 | 每次 poll work 后写 |
| busy_since | 已有 `busy_started_at` | 直接复用 |
| cycles_total | 新增 | 每 cycle 末尾累加 |
| reminds_sent | 新增 | 每 cycle 末尾累加 |
| skips_unchanged | 新增 | 每 cycle 末尾累加 |
| errors_last_n | 新增 | 每 cycle 末尾累加（用 `last_cycle_error` 触发时 +1） |
| state | 视图层派生 | live/stale/dead 视图命令计算 |

## 设计收敛

✅ **复用既有 `state["personas"][persona"]`** — 不新建 `.map/waker-state.json`：
- KISS：避免双源数据漂移
- atomic write：复用既有 `_save_state_if_needed(force=True)`（已用 `bridge_state.save_bridge_state` 含 atomic rename）
- 重启归档：`_check_busy_crash_recovery()` 已有 stale busy 检测机制 → 扩展为归档 `.stale.<ts>.json`

✅ A1 acceptance 仍满足（单源 .map/simple-waker-state-{persona}.json + atomic write + 视图只读）

## 风险

- `_save_state_if_needed(force=True)` 既有调用点多（cycle 末尾、busy 检测等）→ 新字段写入合并到既有 force-save 路径，不引入新 IO
- 视图命令 `map waker status` 只读 `map work` + state.json，不写
