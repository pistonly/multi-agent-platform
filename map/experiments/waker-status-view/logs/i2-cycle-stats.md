# I2 — waker 自写 cycle 统计字段（cli/simple_waker.py）

## 改动范围

仅 `cli/simple_waker.py`：

1. 新增 `_accumulate_cycle_stats(stats)`：每个 cycle 累加 persona_state 字段
   - `pid`（每次覆盖，restart 后视图自动反映）
   - `started_at`（首次 setdefault，view 派生 uptime）
   - `cycles_total`（累加 +1）
   - `reminds_sent_total`（累加 stats.reminds_sent）
   - `skips_unchanged_total`（累加 stats.remind_skips_unchanged）
   - `errors_last_n` + `errors_last_n_window`（最近 10 cycles 滚动窗口）
   - `last_cycle_at` / `last_poll_at`（每次覆盖）
2. 新增 `_archive_and_reset_on_restart()`：A5 重启归档
   - 检测 `pid` 变化 → 写 `.stale.<ts>.json` sidecar（previous_pid + 关键计数）
   - 主 state 文件保持原地（atomic write 契约不破坏）
   - 重置 `cycles_total / reminds_sent_total / skips_unchanged_total / errors_last_n* / started_at / last_cycle_at / last_poll_at / busy_started_at / session_busy_since / busy_pid`
   - **保留** runtime session 字段（`claude_session_id` / `runtime_session_id` / `runtime_contract_hash`）——重启不破坏 runtime 状态连续性
3. `_run_once_async` 起始处紧跟 `_scan_stalled_experiment_locks` 调用 `_accumulate_cycle_stats(stats)`——确保所有 return 路径（remind / dry-run / no-remind）都累加计数

## 设计取舍

- **不引入新 IO**：复用既有 `_save_state_if_needed` 路径（atomic write 已由 `bridge_state.save_bridge_state` 含 tmp + rename 保障）
- **errors 滚动窗口 10 cycles**：避免老错误拖累新错误信号；sum 落在 `errors_last_n` 字段供 view 直接读
- **sidecar `.stale.<ts>.json`**：轻量 marker JSON（仅 previous_pid + 关键计数），不复制全 state（避免 state 文件膨胀）
- **busy 标记也重置**：A5 restart 后 `busy_started_at` 应清零，避免新 waker 假性 busy；与 `_check_busy_crash_recovery` 职责互补（前者进程已死清零，本方法进程重启清零）

## 边界核验

- 与 T2 b3ec2e4d `_touch_busy` / `_clear_busy` 无冲突：本方法只写 `pid`/`cycles`/`reminds`/`skips`/`errors`/`timestamps` 字段，不动 `busy_*` 字段（除 A5 重置时一次性清零）
- 与 T4 d0c9dc5f `_runtime_contract_hash` 漂移检测无冲突：`started_at` setdefault 写在漂移检测之前，漂移触发 reset 时不会清掉本方法新增字段（runtime reset 路径独立）
- 与 `sync_runtime_skills` 镜像机制无冲突：waker state 文件（`.map/simple-waker-state-{persona}.json`）从未在 sync 白名单内

## 实测

```
.venv/bin/python3 -m ruff check cli/simple_waker.py  → All checks passed!
.venv/bin/python3 -m pytest tests/test_simple_waker.py -q  → 37 passed
```

未跑 waker 实启（受单 wake 周期限制，留待 I5 验收）。

## 下一步

- I3：`map waker status` 视图 CLI（cli/commands/waker_status.py 新建）— 渲染 10 字段 + state 三档 + busy 卡死升级
- I4：≥5 case 回归测试（tests/test_waker_status.py 新建）— live / stale / dead / busy-stale / state-archive
- I5：commit + complete + release lock
