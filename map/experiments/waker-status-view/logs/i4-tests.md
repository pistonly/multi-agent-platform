# I4 — ≥5 case 回归测试

## 新建

`tests/test_waker_status.py`（**15 个 case**，超过 plan ≥5 要求）：

| # | case | 覆盖 acceptance |
|---|------|-----------------|
| 1 | `test_state_live_when_recent_poll` | A2 live（gap ≤ 30s）|
| 2 | `test_state_stale_when_gap_in_window` | A2 stale（30s < gap ≤ 300s）|
| 3 | `test_state_dead_when_pid_dead` | A2 dead（pid 不存在）|
| 4 | `test_state_dead_when_pid_missing` | A2 dead（pid 字段缺失）|
| 5 | `test_state_dead_when_last_poll_missing` | A2 dead（旧 state 未带新字段）|
| 6 | `test_busy_stuck_upgrades_to_stale` | A2 busy 卡死升级（busy_since > 5min）|
| 7 | `test_waker_restart_archives_state_and_resets_counters` | A5 pid 变化 → sidecar + 重置；runtime session 字段保留 |
| 8 | `test_waker_no_archive_when_pid_unchanged` | A5 反例：pid 同 → 不归档 |
| 9 | `test_waker_accumulates_errors_in_rolling_window` | A3 errors_last_n 滚动窗口 |
| 10 | `test_waker_errors_window_caps_at_10` | A3 errors_last_n_window 上限 10 |
| 11 | `test_state_live_without_busy_when_recent` | A2 busy_since 缺失 → 普通 gap 判定 |
| 12 | `test_render_table_has_exactly_10_columns` | A3 字段最小集 10 硬上限 |
| 13 | `test_collect_waker_status_is_read_only` | A4 视图只读（无 write IO）|
| 14 | `test_render_emits_warn_for_dead_state` | 视图 WARN 行 |
| 15 | `test_collect_finds_additional_persona_files` | 扫所有 persona state 文件 |

## 已有测试同步更新

`tests/cli/test_compat.py` 和 `tests/cli/test_dry_run_write_commands.py`：

1. `test_compat.py::test_cli_commands_directory_inventory`：新增 `waker_status.py` 到 `EXPECTED_SUBAPP_FILES`（hardcoded inventory 守卫）
2. `test_dry_run_write_commands.py`：
   - 新增 `waker_app: ("waker",)` 到 `_APP_VAR_TO_PATH`（sub-app 登记守卫）
   - 新增 `("waker", "status")` 到 `_READ_ONLY_COMMANDS`（只读命令守卫）

## 实测

```
.venv/bin/python3 -m pytest tests/test_waker_status.py -q  → 15 passed
.venv/bin/python3 -m pytest tests/test_simple_waker.py tests/cli/test_dry_run_write_commands.py tests/cli/test_compat.py -q  → 80 passed
.venv/bin/python3 -m ruff check tests/test_waker_status.py tests/cli/test_dry_run_write_commands.py tests/cli/test_compat.py  → All checks passed!
```

全量基线 1803 → 1818（新增 15 - 0 删除 = +15，0 failed）。

## 关键测试细节

- **`compute_waker_state`** 纯函数测试：通过构造 persona_state dict + 注入 `now` 时间，覆盖 live/stale/dead/busy_stale/never 五态
- **`_accumulate_cycle_stats` + `_archive_and_reset_on_restart`**：用 `SimpleWaker.__new__(SimpleWaker)` 跳过 `__init__`，手动注入 state_file + state dict，模拟 pid 变化场景
- **`os.getpid()` 当活 pid**：用 `2_000_000_000` 标记"明确不存在的 pid"避免与当前进程冲突；live case 必须用 `os.getpid()` 否则会因 pid 已死 → dead
- **`errors_last_n_window`**：构造 8 cycle（前 5 错 / 后 3 干净）确认 sum=5；构造 15 cycle 全错确认 window 被截断到 10，sum=10

## 下一步

- I5：commit + complete + release lock
