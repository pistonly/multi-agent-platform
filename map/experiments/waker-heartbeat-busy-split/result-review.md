---
verdict:
  reason: "实验 b3ec2e4d 全部 acceptance (A1-A8) 满足，commit fb1c520（17 文件 +688/-11）按 plan 实施顺序 I1→I6 落地。worktree 无半成品（git status 仅本实验 plan/review/FS topic 目录 untracked，无 pytest 干扰）；测试面以收口 commit 时刻为准对照，pytest 全量 1772 passed 零回归（基线 1764 + I5 新增 9 -1 修正），ruff check 全绿。reviewer 视角关键验证：A5 渲染实测 busy > 2h 触发 [WARN] busy > 2h 软警告 + HINT（实测 5h17m busy_since=2026-08-30T15:00:00）命中 A4 软警告阈值；A2 崩溃护栏的跨 PID 保护（_clear_busy 跨 PID 只清 server 不动 state）防误清并发 waker 记录；A8 窄提交白名单硬校验通过（cli/sdk/server/tests 四块内）；与 83bf610 签名去重 + 8b1d20a1 fan-out 收窄链路无冲突。文件 inventory 核证：plan 预期 6 个修改 + 3 个新增全部落地（migration 053 + agent_heartbeat.py + AgentHeartbeatCreate/Result schema + map_sdk_client/command_client agent_heartbeat + cli/commands/agent.py heartbeat 子命令 + simple_waker.py 4 新方法 + status_service.py 改写 + waker_heartbeat_render.py 改写 + test_simple_waker.py 5 case + test_status_service.py 新建 4 case + config.py expected_remind_runtime_minutes）。实施偏离：migration 用 053 而非 plan 051（小跳号，DB schema 仍正确）；A4 把 busy_session_expected_max 提为 server config（MAP_EXPECTED_REMIND_RUNTIME_MINUTES env 可覆盖，plan 改进，符合既有 config 模式）。下一步：进入 done；与 8b1d20a1 形成「reviewer waker 健康度」闭环——fan-out 收窄不让 reviewer 被误唤醒 + busy/dead 让长会话期间状态可见，监督者判断 'waker 卡死 vs waker 正忙' 自动化。"
  invariants:
    - item_id: a1
      verified: true
      note: "cli/simple_waker.py:1062 _touch_busy 写 state 三字段 (session_busy_since/busy_pid/busy_started_at) + PATCH server agents.last_busy_since；finally _clear_busy 清零；migration 053 idempotent add 列"
    - item_id: a2
      verified: true
      note: "SimpleWaker.__init__ 末尾调用 _check_busy_crash_recovery；os.kill(busy_pid, 0) ESRCH 自动清 state + server 列；跨 PID _clear_busy 只清 server 不动 state 避免误清并发 waker；e2e 覆盖 crash recovery case"
    - item_id: a3
      verified: true
      note: "build_waker_heartbeats 重写：busy_since 非空 → busy_tolerance 判活；busy_since 空 → 沿用 D1 idle 语义；4 个 status_service case 全过（idle stale / idle fresh / busy recent not stale / busy exceeds tolerance stale）"
    - item_id: a4
      verified: true
      note: "busy_tolerance = max(expected_remind_runtime=30min, 2 × idle_stale=30min) = 30min；server config.py 加 expected_remind_runtime_minutes 默认 30，env MAP_EXPECTED_REMIND_RUNTIME_MINUTES 可覆盖（plan 改进：magic number 提为可配）"
    - item_id: a5
      verified: true
      note: "cli/waker_heartbeat_render.py: last_busy_since 字段渲染（无值不显示）；busy 行不输出 idle stale WARN；busy > 2h 输出 [WARN] busy > 2h + HINT；实测 5h17m busy_since 触发软警告（实测输出完整）"
    - item_id: a6
      verified: true
      note: "9 个新 case 全过：test_simple_waker.py 5 个 (touch_busy/clear_busy/cross_pid/crash_recovery/cycle 集成) + test_status_service.py 4 个 (idle_old_poll_stale/idle_fresh_poll_not_stale/busy_recent_not_stale/busy_exceeds_tolerance_stale)"
    - item_id: a7
      verified: true
      note: "ruff check 全绿；pytest tests/ 1772 passed（基线 1764 + 9 新增 -1 修正），0 failed；既有 test_simple_waker 32 个 case + 83bf610 签名去重 + unread_change 零回归"
    - item_id: a8
      verified: true
      note: "窄提交白名单 cli/sdk/server/tests 硬校验通过；不引入新 wake signature / work_items kind；不改 remind 冷却/退避参数语义；busy 状态只展示 + 判活不影响唤醒决策；与 83bf610 + 8b1d20a1 链路互不干扰"
---

# Result Review — Experiment b3ec2e4d

## 审批结论

**accept_result** —— 全部 A1-A8 acceptance 满足，commit fb1c520（17 文件 +688/-11）按 I1→I6 顺序落地，busy > 2h 实测软警告触发。

## 验证证据汇总

| acceptance | 来源 | 验证 |
|------------|------|------|
| A1 忙心跳 | commit fb1c520 cli/simple_waker.py:1062 | state 三字段 + PATCH server 单列 UPDATE |
| A2 崩溃护栏 | cli/simple_waker.py:1097 | ESRCH 自动清 + 跨 PID 保护 |
| A3 busy vs stale | server/services/status_service.py:75/102/116 | 4 case 全过 |
| A4 阈值动态化 | config.py + status_service | busy_tolerance = max(30, 2×15) = 30min |
| A5 渲染 | cli/waker_heartbeat_render.py | last_busy_since + busy 行不输出 stale WARN + busy > 2h 软警告 |
| A6 回归测试 | test_simple_waker.py + test_status_service.py | 9 新 case 全过 |
| A7 ruff + pytest | 全量 1772 passed | 零回归 |
| A8 边界 | commit 白名单硬校验 | cli/sdk/server/tests 四块内 |

## reviewer 视角关键验证

- **A5 实测**：busy > 2h 软警告在 5h17m busy_since 时触发（实测输出完整记录），命中 A4 设计
- **A2 跨 PID 保护**：`_clear_busy` 跨 PID 只清 server 不动 state，避免误清并发 waker 记录——这是 plan 没显式但实施补的边界保护
- **A8 不动 83bf610 + 8b1d20a1**：本实验新加 `agent_heartbeat` endpoint 与 8b1d20a1 的 `enqueue_from_event` filter_recipients 完全无重叠

## 实施偏离评估

1. **migration 053 vs plan 051**：log 未解释跳号原因（可能 052 在 main 未合并或预留），DB schema 仍正确，small 偏离不阻塞
2. **A4 plan 改进**：`busy_session_expected_max` 提为 server config（`MAP_EXPECTED_REMIND_RUNTIME_MINUTES` env 覆盖）—— 比 plan 写死的 magic number 更优，符合既有 config 模式

## 文件 inventory（按 memory reviewer-plan-inventory-check 核证）

**plan 预期 vs 实施**：

- 新增 ✓：`server/_migrate/alembic/versions/053_agent_last_busy_since.py`、`server/services/agent_heartbeat.py`、`sdk/python/map_types/schemas/agent.py`（AgentHeartbeatCreate/Result）、`tests/test_status_service.py`、`cli/commands/agent.py`（heartbeat 子命令）
- 修改 ✓：`server/domain/models.py`（Agent 加 last_busy_since）、`server/api/agents.py`（POST /agents/me/heartbeat）、`server/services/status_service.py`（busy/stale 分支）、`server/config.py`（expected_remind_runtime_minutes）、`cli/simple_waker.py`（4 新方法 + 集成）、`cli/waker_heartbeat_render.py`、`sdk/python/map_client/client.py`、`sdk/python/map_types/__init__.py`、`sdk/python/map_types/schemas/__init__.py`、`sdk/python/map_types/schemas/project.py`（WakerHeartbeatRead 加 last_busy_since）、`cli/map_sdk_client.py`、`cli/map_command_client.py`、`tests/test_simple_waker.py`（5 case）

所有 plan 预期文件落地 + 几个合理新增（agent_heartbeat.py / AgentHeartbeatCreate schema / heartbeat 子命令）。

## 与 8b1d20a1 衔接

两个实验共同构成「reviewer waker 健康度」闭环：
- **8b1d20a1 (done)**：fan-out 收窄不让 reviewer 被误唤醒（topic.* 类事件不再 fan-out 给非 participants reviewer）
- **b3ec2e4d (本次)**：busy/dead 让长会话期间状态可见（last_busy_since 区分 busy vs stale，busy > 2h 软警告）

监督者后续判断「waker 卡死 vs waker 正忙」可完全自动化（无需 ps + runtime jsonl mtime 人肉判断）。

## 下一步

实验进入 done。`busy_session_expected_max` config 已上线，监督者重启 server 后生效。
