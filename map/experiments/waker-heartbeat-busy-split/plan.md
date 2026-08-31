---
title: "waker 心跳 busy/dead 拆分：长会话期间心跳与忙状态分离"
acceptance:
  - "A1 忙心跳写入与清理：`SimpleWaker._run_once_async` 在 `backend.wake_async` 前 touch `session_busy_since`（写 waker state 文件 + PATCH server `agents.last_busy_since`），`finally` 清零；写 busy 时同时写 `busy_pid` + `busy_started_at` 到 state 文件"
  - "A2 崩溃护栏：waker 启动时（`_run_forever_async` 起点）`os.kill(busy_pid, 0)` 自检——进程已不在（ESRCH）视为上次崩溃，自动清掉 busy 标记；`try/finally` + PID 自检双保险覆盖正常清理 + SIGKILL/OOM/断电"
  - "A3 busy vs stale 区分：`server/services/status_service.build_waker_heartbeats` 把 `last_busy_since` 加进 `WakerHeartbeatRead`；`stale` 计算：busy_since 非空 → busy 不算 stale（按 busy 容忍阈值判活）；busy_since 空 + last_waker_poll_at 超过 `waker_stale_threshold_minutes` → stale"
  - "A4 阈值动态化：busy 容忍 = `max(busy_session_expected_max, 2 × waker_stale_threshold_minutes)`；超过 2h 输出 `[WARN] busy > 2h, check session health`；busy 状态不触发 wake（remind 决策不变）"
  - "A5 `map work` 渲染：`cli/waker_heartbeat_render.py` 新增 `last_busy_since` 字段（无值不显示）；busy 行不输出 stale WARN；busy > 2h 输出软警告 `[WARN] busy > 2h`"
  - "A6 回归测试强制：`tests/test_simple_waker.py` 新增两类 case：(a) 短 fake busy (N=5-10s) 验证 busy 标记写入/清理 + last_busy_since 落库；(b) 长 fake busy (N=3-5 分钟) 验证 busy 容忍阈值生效 + busy > stale 阈值后正确回到 stale 路径；`tests/test_status_service.py`（如缺则新建）新增 build_waker_heartbeats busy/stale 分支覆盖"
  - "A7 验收：`ruff check` 通过；`pytest tests/ -q` 全绿（基线以开实验时 HEAD 为准，只增不减，0 failed）；既有 `tests/test_simple_waker.py` 签名去重（`wake_signature` / `max_silence_seconds`）与 unread_change 测试零回归"
  - "A8 边界：不引入新的 wake signature / work_items kind（与 83bf610 + 8b1d20a1 fan-out 收窄去重链路互不干扰）；不改 remind 冷却/退避参数语义；busy 状态只展示 + 判活，不影响唤醒决策；窄提交白名单 `^cli/simple_waker.py` / `^server/` / `^tests/` / `^.cursor/skills/`"
evidence_keys:
  - "pytest_summary:test_simple_waker 新增 busy case + test_status_service 新增 busy/stale 分支；全量 pytest -q 0 failed"
  - "实测输出:模拟长 fake busy（fake backend.wake_async 阻塞 3-5 分钟），期间调 `map work` 心跳面板显示 busy 不显示 stale；会话结束 busy_since 清零，回归正常轮询心跳"
  - "grep 核证:cli/simple_waker.py 含 `_touch_busy` / `_clear_busy` / `_check_busy_crash_recovery` 函数；server/services/status_service.py 含 last_busy_since 字段；server/domain/models.py 含 last_busy_since 列"
dependencies:
  - "话题 waker-heartbeat-busy-split（97dfb82a-d727-5401-8b3a-e4a9671c4a5a）Round 1 Summary 收口"
  - "既有 last_waker_poll_at（D1，server/_migrate/alembic/versions/050）+ last_api_seen_at 心跳路径不动；本实验新增 last_busy_since 列（类似 D1 模式 + idempotent guard）"
  - "既有 status_service.py build_waker_heartbeats stale 计算 + waker_heartbeat_render banner 渲染路径不动；本实验仅扩展字段与 busy/stale 分支"
  - "83bf610 签名去重（wake_signature + max_silence_seconds）+ 8b1d20a1 fan-out 收窄语义不动；本实验不引入新的去重路径"
  - "本任务 cli/ 改动（simple_waker.py 加 busy 状态机 + 新 heartbeat endpoint 客户端）；验收后由监督者重启 server + waker 生效"
---

# waker 心跳 busy/dead 拆分：长会话期间心跳与忙状态分离

## 背景

expert 仓战役与平台仓首日反复踩到同一问题：waker 进入长 remind 会话（claude runtime 执行实验，实测 40+ 分钟）期间轮询循环阻塞，`last_waker_poll_at` 停止更新，`map work` 心跳面板报 stale 警告。监督者（人或自动巡检）无法区分「waker 卡死」与「waker 正忙」，只能用 `pstree` 找 claude 子进程 + 看 runtime session jsonl 的 mtime 人肉判断——两天内出现 ≥4 次误报性 stale，全部靠人工核实排除。

## 根因

`cli/simple_waker.py` 只在轮询 cycle 结束时更新心跳/状态；进入 runtime 调用（remind → claude 子进程）期间没有任何「我还活着，只是在忙」的信号写出。心跳语义把「最后一次轮询完成时间」和「waker 进程活性」混为一谈。

## 任务

1. **忙心跳**：waker 在发起 remind（进入 runtime 调用）前 touch 心跳状态——waker state 文件新增 `session_busy_since` + `busy_pid` + `busy_started_at`；server 新增 `agents.last_busy_since` 列（migration）；waker 在 busy start/end 时 PATCH server endpoint。会话结束清除。
2. **崩溃护栏**：启动时 `os.kill(busy_pid, 0)` 自检——ESRCH 视为上次崩溃，自动清掉 busy 标记；`try/finally` + PID 自检双保险
3. **展示区分**：`map work` 心跳面板区分 busy 与 stale；busy 容忍值 = `max(busy_session_expected_max, 2 × waker_stale_threshold_minutes)`（≥ 2× idle stale）；busy > 2h 输出软警告；busy 状态不触发 wake
4. **窄提交白名单**：`^cli/simple_waker.py`、`^server/`、`^tests/`、`^.cursor/skills/`

## 实施步骤

### I1 busy 状态机（cli + server schema + endpoint）

- `server/_migrate/alembic/versions/051_agent_last_busy_since.py`：idempotent add `agents.last_busy_since`（DateTime nullable）
- `server/domain/models.py`：Agent 加 `last_busy_since: Mapped[datetime | None]`
- `server/api/agents.py`：新增 `POST /agents/me/heartbeat` body `{busy_since: ts | null}`，更新 agents.last_busy_since
- `server/services/agent_heartbeat.py`（新建）：封装 heartbeat PATCH 逻辑（与现有 last_waker_poll_at 解耦）

### I2 waker busy 状态机（cli/simple_waker.py）

- `SimpleWaker._run_once_async`：在 `backend.wake_async` 前调 `_touch_busy`，`finally` 调 `_clear_busy`
- `_touch_busy`：写 state 文件 `session_busy_since` + `busy_pid` + `busy_started_at`，PATCH server
- `_clear_busy`：清 state 文件，PATCH server `busy_since=null`
- `_check_busy_crash_recovery`：`_run_forever_async` 起点调 `os.kill(busy_pid, 0)`；ESRCH → 自动清 busy
- `MapCommandClient` / `MapSdkClient`：新增 `agent_heartbeat(busy_since=ts)` 方法

### I3 busy/stale 计算（server/services/status_service.py）

- `build_waker_heartbeats`：返回 `last_busy_since` 字段
- `stale` 计算分支：busy_since 非空 → busy 不算 stale；busy_since 空 → 沿用旧逻辑（last_waker_poll_at vs threshold）
- `WakerHeartbeatRead` schema 加 `last_busy_since` 字段
- busy 容忍 = `max(busy_session_expected_max, 2 × waker_stale_threshold_minutes)`；> 2h 输出 WARN（render 层）

### I4 渲染（cli/waker_heartbeat_render.py）

- 每行新增 `last_busy_since` 字段（无值不显示）
- busy 状态：行尾加 `busy` 标记，不输出 stale WARN
- busy > 2h：`[WARN] busy > 2h, check session health`
- `[HINT]` 文案保留

### I5 回归测试（tests/）

- `tests/test_simple_waker.py`：
  - (a) 短 fake busy：fake backend.wake_async 阻塞 5s，验证 `_touch_busy` / `_clear_busy` 调用 + state 文件含 busy_since + busy_pid + last_busy_since PATCH server 2 次
  - (b) 长 fake busy：fake backend.wake_async 阻塞 3 分钟（用 patch 缩短 busy 容忍阈值），验证 busy > 2h 软警告 + busy 状态下 stale=False
  - 崩溃恢复：fake state 文件含 `busy_pid=<dead_pid>`，启动时 `_check_busy_crash_recovery` 自动清 busy
- `tests/test_status_service.py`（如缺新建）：
  - busy_since 非空 + last_waker_poll_at 旧 → stale=False
  - busy_since 空 + last_waker_poll_at 旧 → stale=True
  - 阈值边界（busy 容忍 = max(expected, 2×idle_stale)）
- 既有签名去重（`wake_signature` / `max_silence_seconds`）+ unread_change 测试零回归

### I6 commit + log + release

- 窄 commit 白名单校验：`git diff --name-only` 必须落在 `^cli/simple_waker.py` / `^server/` / `^tests/` / `^.cursor/skills/`
- `pytest -q` 全量绿（基线 1764 passed + 新增，只增不减）
- complete log → reviewer → done

## 风险与边界

- DB schema 不引入新表；仅 agents 表加 1 列（migration）
- 既有 last_waker_poll_at 路径不动（与 last_busy_since 并行）
- 既有 status_service stale 计算 + waker_heartbeat_render 渲染逻辑只在末尾扩展，不改既有分支
- 不引入新的 wake signature / work_items kind
- 不影响 remind 冷却/退避参数语义
- busy 状态只展示 + 判活，不影响唤醒决策
- cli/ 改动：验收通过后由监督者重启 server 与 waker 生效

## Co-author

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
