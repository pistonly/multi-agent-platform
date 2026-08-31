---
title: "waker 巡检视图（map waker status）：waker 自写 state + 视图只读"
acceptance:
  - "A1 单源：waker 启动 + 每 cycle 末尾 atomic write `.map/waker-state.json`（先 .tmp 再 rename）；视图命令 `map waker status` 只读该 JSON + `map work` 心跳字段合成视图；不复用 OS 视角（pgrep/pstree）"
  - "A2 stale 三档：`live` = `now - last_poll_at ≤ 30s`（cycle 周期）；`stale` = 30s < gap ≤ 300s；`dead` = gap > 300s 或 pid 不存在。busy_since > 5min 自动升级 stale（防'卡死 busy'逃过巡检）"
  - "A3 字段最小集 10 字段硬上限：`persona | pid | uptime | last_poll | busy_since | cycles | reminds | skips | errors | state`；漏 `errors_last_n` 比多字段严重（底线）"
  - "A4 视图只读：`map waker status` 禁止写日志 / 发通知 / 重启 waker / 改 state；与 T1-T4 单向流一致（视图是末梢，不闭环回去）"
  - "A5 重启归档：waker 启动时检测到旧 state.json（pid 不同或过期） → 归档为 `.stale.<ts>.json` + 新 state.json 起始 cycles=0；视图不混入旧数据"
  - "A6 回归测试 ≥5 case（tests/test_waker_status.py 新建）：live 渲染 / stale 渲染 / dead 渲染 / 卡死 busy_since=10min 升级 stale / 旧 state 归档 + cycles=0"
  - "A7 ruff check 0；pytest 全量绿（基线 1791 passed / 2 skipped / 359 deselected，只增不减，0 failed）；git diff 白名单 `^cli/`、`^tests/`"
  - "A8 边界：sync_runtime_skills 不动（不镜像 waker-state.json）；与 T1 8b1d20a1 签名去重、T2 b3ec2e4d busy 拆分、T4 d0c9dc5f drift 自检已落地链路无冲突；不引入新 server 端点 / DB 字段"
evidence_keys:
  - "pytest_summary:新增 ≥5 case 全过（tests/test_waker_status.py 或追加到 test_simple_waker.py），全量 pytest -q 0 failed"
  - "实测输出:(1) waker 启动 → .map/waker-state.json 出现且 atomic write 验证；(2) cycle 末尾 → cycles/reminds/skips/errors 累加；(3) mock pid 缺失 → state=dead；(4) mock busy_since=10min 前 → state=stale；(5) 重启 waker → 旧 state.json 归档为 .stale.<ts>.json，新 cycles=0"
  - "grep 核证:cli/simple_waker.py 含 _save_waker_state + atomic rename；cli/waker_status_cmd.py (或 cli/commands/ 下新模块) 含 waker status 视图渲染；tests/ 含 live/stale/dead/busy-stale/state-archive case"
dependencies:
  - "话题 waker-status-and-cost-ledger（7f0e8de7）Round 1+2 共识收口"
  - "现有 cli/simple_waker.py:_save_state_if_needed 与 .map/simple-waker-state.json（既有 runtime session + remind timestamps，不重叠）"
  - "现有 waker 心跳接口 b3ec2e4d 落地的 GET /agents/me/todos (heartbeat + busy)"
  - "现有 cli/waker_heartbeat_render.py（T2 渲染层）— 本次只复用数据不复用渲染"
  - "既有 sync_runtime_skills 全量镜像机制（实验 d0c9dc5f）—— waker-state.json 明确划入镜像边界外"
  - "验收通过后由监督者重启 waker 生效（无 docker 镜像 build，仅 daemon restart）"
---

# waker 巡检视图（map waker status）

## 背景

本战役（T1-T4）巡检靠拼四件套——`pgrep` 数进程 / `map work --notification-category wakeable` 看心跳 / `tail .map/waker-logs/*.log` 看 cycle 统计 / `pstree` 查忙会话。T2 落地后 `map work` 已有 heartbeat + busy，但 **waker 进程活性（pid / 启动时长 / cycle 计数 / remind_sent / skips / errors）仍无单一入口**。监督者 30 分钟巡检耗时难以压缩。

## 任务

新增 `map waker status` 单命令视图：
- waker 自写 `.map/waker-state.json`（atomic write：`.tmp` + rename）
- 每 cycle 末尾更新 `{cycles_total, reminds_sent, skips_unchanged, errors_last_n, last_cycle_at}`
- 视图命令读 state JSON + `map work` 心跳字段合成视图
- stale 三档 + busy 卡死升级 + 字段最小集 10 + 视图只读

## 实施步骤

### I1 调研既有 state 文件

- `.map/simple-waker-state.json` 已存在（runtime session + remind timestamps，与 waker-state.json 不同职责，**不合并**）
- `.map/waker-state.json` 新建——waker 进程级共享状态（persona / pid / uptime / cycle 统计）
- 字段冲突检查：与既有文件无字段重叠

### I2 waker 写 state（cli/simple_waker.py）

- `_save_waker_state()` helper：atomic write（先写 `.tmp` 再 rename）
- 启动时调用一次：`{persona, pid, started_at, cycles_total=0, ...}`
- 每 cycle 末尾调用：累加 `{cycles_total, reminds_sent, skips_unchanged, errors_last_n, last_cycle_at}`
- 旧 state 启动时检测：pid 不同 / mtime 过老 → 归档 `.stale.<ts>.json`

### I3 视图渲染（cli/commands/waker_status.py 新建）

- 命令 `map waker status`
- 字段最小集 10：`persona | pid | uptime | last_poll | busy_since | cycles | reminds | skips | errors | state`
- state 计算：live ≤ 30s / stale 30s-300s / dead > 300s 或 pid 缺失
- busy_since > 5min 自动升级 stale
- 只读：不写日志 / 不发通知 / 不重启 waker / 不改 state

### I4 回归测试（tests/test_waker_status.py 新建 ≥5 case）

- (1) live 渲染：cycle 30s 内 → state=live
- (2) stale 渲染：cycle 60s 前 → state=stale
- (3) dead 渲染：pid 不存在 → state=dead
- (4) 卡死 busy_since=10min 前 → state=stale 而非 live
- (5) 旧 state 归档 + 新 cycles=0

### I5 commit + log + release

- 窄 commit 白名单 `^cli/`、`^tests/`
- ruff check 0 + pytest 全量绿
- complete log → reviewer → done

## 风险与边界

- 不改 sync_runtime_skills 全量镜像语义（waker-state.json 明确划入镜像边界外）
- 不引入新 server 端点 / DB 字段
- 不影响 T1-T4 已落地链路（只读数据源）
- 视图是末梢，不闭环回去——避免把巡检工具变成"自检触发器"
- 不引入外部依赖（如 psutil）；纯 fs 读 + map CLI 调

## Co-author

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
