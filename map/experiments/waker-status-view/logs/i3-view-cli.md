# I3 — `map waker status` 视图 CLI

## 改动范围

3 个文件：

1. `cli/waker_status_view.py`（新建）— 纯渲染函数
   - `compute_waker_state(persona_state, *, now=None) -> str`：派生 live/stale/dead/busy_stale
   - `collect_waker_status(state_dir, *, now=None) -> list[dict]`：扫 `.map/simple-waker-state-*.json` 收集行
   - `render_waker_status_table(rows) -> str`：markdown 表 + 警告行
   - 内部 `_pid_alive(pid)` / `_parse_dt(value)` / `_format_uptdelta` helpers

2. `cli/commands/waker_status.py`（新建）— `map waker status` sub-app
   - `@waker_app.callback()`：强制多命令模式（与 host_app 同款约定）
   - `@waker_app.command("status")`：支持 `--project-root` / `--json` / `--stale-threshold-seconds`

3. `cli/main.py`（注册）— `app.add_typer(waker_app, name="waker")`

## 字段最小集（10 字段硬上限）

```
persona | pid | uptime | last_poll | busy_since | cycles | reminds | skips | errors | state
```

完整字段（含内部）：
- `persona` / `pid` / `pid_alive` / `uptime` / `last_poll_at` / `last_poll_gap_s` / `busy_since` / `cycles_total` / `reminds_sent_total` / `skips_unchanged_total` / `errors_last_n` / `state`

## state 派生（A2 + busy 卡死升级）

| 优先级 | 条件 | state |
|--------|------|-------|
| 1 | `busy_since` 距今 > 5min + pid alive | `stale`（卡死 busy 升级）|
| 2 | pid 缺失或已死 | `dead` |
| 3 | `last_poll_at` 缺失 | `dead`（首次启动前/旧 state）|
| 4 | gap ≤ 30s | `live` |
| 5 | 30s < gap ≤ 300s | `stale` |
| 6 | gap > 300s | `dead` |

## 实测

```bash
.venv/bin/python3 -m ruff check cli/waker_status_view.py cli/commands/waker_status.py cli/main.py  → All checks passed!
.venv/bin/python3 -m cli.main waker status  → 渲染 3 行 + 警告 + HINT（正确反映现状：pid/cycles 字段尚未被新 waker 写入 → 全 dead）
.venv/bin/python3 -m cli.main waker status --json  → JSON 模式输出 3 rows + stale_threshold_seconds + generated_at
```

## 视图只读（A4）核验

- `map waker status` 路径上无任何写 IO（仅 read `.map/*.json` + stdout echo）
- 无 server 调用（不发通知 / 不调 client.* API）
- 无 restart / signal / subprocess 操作

## 与 T2/T4 已有视图的边界

- `map work --notification-category wakeable`：server-side 心跳视图（依赖 server PATCH）
- `map work`（默认）：server-side todos + notifications
- `cli/waker_heartbeat_render.render_waker_heartbeat_banner`：`map work` 顶部 stderr 横幅（复用 server `/status` 数据）
- **`map waker status`（本实验 I3 新增）**：客户端 waker state 视图（直接读 `.map/` JSON，不依赖 server）

三层视角分工：
- server `agents.last_waker_poll_at` / `agents.last_busy_since`：观察 agent 进程「最近一次心跳」
- 客户端 waker state：本 waker 实例的 cycle 统计（pid / cycles_total / errors_last_n）
- 二者合并 = 完整 waker 巡检视图

## 下一步

- I4：`tests/test_waker_status.py` ≥5 case（live / stale / dead / busy-stale / state-archive）
- I5：commit + complete + release lock
