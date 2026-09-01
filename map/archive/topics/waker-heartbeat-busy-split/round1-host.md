---
author: host
round: 1
kind: user
is_round_summary: true
posted_at: '2026-08-30T19:28:40.032624+00:00'
---

# Round 1 Summary（host 收口）

## 已共识

- **T1 忙心跳 + session_busy_since**：必做 + 加崩溃护栏
  - 写 busy 时同时写 `busy_pid` + `busy_started_at`
  - 下次轮询 cycle 启动时 `os.kill(busy_pid, 0)` 自检：进程已不在（ESRCH）→ 视为上次崩溃，自动清掉 busy 标记
  - `try/finally` + PID 自检双保险，覆盖正常清理与 SIGKILL/OOM/断电
- **T2 busy vs stale 展示区分**：必做 + 阈值动态化 + 软警告
  - busy 容忍值 = `max(busy_session_expected_max, 2 × max_silence_seconds)`（≥ 2× idle stale）
  - busy > 2h 输出 `[WARN] busy > 2h, check session health`
  - `map work` 心跳面板新增 `last_busy_since` 字段（无值时不显示），区分 busy 与 stale
- **T3 窄提交白名单**：`^cli/simple_waker.py`、`^server/`、`^tests/`、`^.cursor/skills/`；本任务**不新增** wake signature 或 work_items kind（避免与 83bf610 签名去重 + 8b1d20a1 fan-out 收窄形成第二条去重链路）

## 验收补充（采纳 participant 第 30-43 行）

- 回归测试两类 case：
  - (a) 短 fake busy（N=5-10s）：验证 busy 标记写入/清理逻辑正确
  - (b) 长 fake busy（N=3-5 分钟）：验证 busy 容忍阈值生效 + busy > stale 阈值后正确回到 stale 路径
- pytest 基线 1764 passed / 359 deselected；本次实验只增不减，0 failed
- 既有签名去重（`wake_signature` / `max_silence_seconds`）与 unread_change 测试不回归
- 窄提交白名单硬校验：`git diff --name-only` 输出必须在白名单内

## 隐含边界（采纳 participant 第 50-56 行）

- 不引入新的 wake signature 路径——与 83bf610 + 实验 8b1d20a1（reviewer waker 空唤醒根治）已落地的签名去重互不干扰
- 不改 remind 冷却/退避参数语义——busy 只展示 + 判活，不影响唤醒决策
- 不动 `wake_signature` / `max_silence_seconds` / `unread_change` 语义

## 未决

- 无。本议题范围明确 + 两轮收敛（host + participant 已表态），可直接开实验

## 下轮议程

- **进入实验**：开「waker 心跳 busy/dead 拆分」实验，按 plan 实施 → 验收 → review → done

## 主持状态

- 开实验：**是**（Round 1 收敛 + 四门 rubric 全过）

---

## 附：Round 1 开场（原任务描述，作为历史引用）

T2：waker 心跳 busy/dead 拆分——长会话期间心跳与「忙」状态分离。

**现象（实测）**：expert 仓战役与平台仓首日反复踩到同一问题：waker 进入长 remind 会话（claude runtime 执行实验，实测 40+ 分钟）期间轮询循环阻塞，`last_waker_poll_at` 停止更新，`map work` 心跳面板报 stale 警告。监督者（人或自动巡检）无法区分「waker 卡死」与「waker 正忙」，只能用 `pstree` 找 claude 子进程 + 看 runtime session jsonl 的 mtime 人肉判断——两天内出现 ≥4 次误报性 stale，全部靠人工核实排除。

**根因**：`cli/simple_waker.py` 只在轮询 cycle 结束时更新心跳/状态；进入 runtime 调用（remind → claude 子进程）期间没有任何「我还活着，只是在忙」的信号写出。心跳语义把「最后一次轮询完成时间」和「waker 进程活性」混为一谈。

**任务**：
1. 忙心跳：waker 在发起 remind 前 touch 心跳状态——`session_busy_since` 字段，会话结束清除
2. 展示区分：`map work` 把「busy（忙 N 分钟）」与「stale（真超时）」区分；busy 容忍 ≥ 2× idle stale
3. 窄提交白名单：`^cli/simple_waker.py`、`^server/`、`^tests/`、`^.cursor/skills/`

## Co-author

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
