# I2+I3 测试 commit 执行日志

> 实验：`41687a01-3992-471b-b415-8ad80f732f80` — waker Phase 2：SSE 叠加 + lifecycle 事件补 publish + 重连补偿
> 当前 plan version：2；I1/I2/I3/I4 已完成（按 I1-I4 log）；本日志覆盖 **commit I2+I3 测试文件**。

## 范围与目标

把 I2+I3 的 3 个新增测试文件 + 3 个 execution log 从 working tree 提交到分支，确保后续 wake
可以基于已 commit 的测试增量推进 I5 端到端验证。

只 commit **测试文件 + execution log**，**不** commit `cli/runtime_waker.py`（原因见下节）。

## 改动清单

| 文件 | 变更 |
|------|------|
| `tests/test_waker_phase2_acceptance.py` | 新增（已 stage → commit）|
| `tests/test_waker_phase2_i2_i3.py` | 新增 |
| `tests/test_waker_phase2_sse_consumer.py` | 新增 |
| `.map/generated-plans/experiment-41687a01-i1-log.md` | 新增 |
| `.map/generated-plans/experiment-41687a01-i2-i3-log.md` | 新增 |
| `.map/generated-plans/experiment-41687a01-i4-log.md` | 新增 |

合计：6 files，+1513 lines。Commit `78682ec test(waker-phase2): I2+I3 SSE acceptance + unit + consumer tests`。

## 验证

commit 前跑完整测试套件（working tree 状态，含 B 实验 uncommitted 代码）：

```
$ python -m pytest tests/test_waker_phase2_i2_i3.py \
                  tests/test_waker_phase2_acceptance.py \
                  tests/test_waker_phase2_sse_consumer.py \
                  tests/test_waker_phase2_i1.py \
                  tests/test_waker_phase1_acceptance.py -q --no-header
......................................................................   [100%]
70 passed in 130.66s (0:02:10)

$ python -m pytest tests/test_b_acceptance.py \
                  tests/test_action_items.py \
                  tests/test_a2_action_item_cascade.py \
                  tests/test_b_action_item_stale.py \
                  tests/test_b_waker_should_wake_action_item.py -q --no-header
.......................................................                  [100%]
55 passed in 84.95s (0:01:24)
```

125 测试全绿（Phase 2 SSE 70 + B 实验 55）。I1 测试文件 `tests/test_waker_phase2_i1.py` 此前
已随 `9551417 feat(waker-phase2): wire lifecycle SSE publish at 4 missing points (I1)` 提交，
本次 commit 不重提。

## 未 commit 部分 + 决策记录

### 1. `cli/runtime_waker.py` 未 commit（+891/-7 行）

**为何不 commit**：该 diff 与实验 B (`1c7fcead`) 的 I4 escalation 代码（`should_wake_action_item`、
`scan_pending_action_items`、`_WAKE_STAGE_HOURS` 等）在同一 hunk 内交织，且顶部 imports
（`timedelta`、`Enum`）是两个实验共用。无法做干净 hunk split，需要 per-hunk surgery 或
`git checkout -p` 手动选段，超出本次 wake 范围。

**风险**：`cli/runtime_waker.py` 当前在 working tree，但不在分支 HEAD。如果 working tree
丢失或 reset，Phase 2 SSE 源码（`_run_sse_loop_async`、D3 backoff、D4 rate limit、
replay 豁免）和 B 实验源码（escalation）会同时丢失。

**当前 working tree 测试状态**：125 测试通过（70 Phase 2 + 55 B），代码可运行。

**下一步决策**（下次 wake 或人工）：
- (a) `git checkout -p HEAD -- cli/runtime_waker.py` 选段，手工把 Phase 2 SSE 段
  （D1/D3/D4 helpers + `_run_sse_loop_async`）从 working tree 拣出 commit；
- (b) 合 B 实验 B-I1/I3/I4/I7 uncommitted 代码 + Phase 2 SSE I2+I3 代码为一个
  recovery commit（B 部分附「recover from B session that closed without commit」
  说明，附 plan/log 链接）；
- (c) 不 commit `cli/runtime_waker.py`，接受 working tree 丢失风险，等下个独立
  实验自然 commit 时捎带。

本次 wake 选 **不 commit cli/runtime_waker.py**，保留(a)/(b)/(c) 三选项给 host 判断。

### 2. B 实验 B-I1/I3/I4/I7 工作未 commit（**严重**）

**发现问题**：实验 B (`1c7fcead`) 经 reviewer accept-result 通过（`phase=done`），
但其主要实现代码从未 commit 到分支：

| 未 commit B 文件 | 大小 | 来源（plan §I） |
|----------------|------|----------------|
| `server/services/action_item_service.py` | 7328B | B-I3 + B-I5 |
| `server/api/action_items.py` | 4707B | B-I7 |
| `cli/runtime_waker.py` 中 `should_wake_action_item` 等 | 见上 | B-I4 |
| `server/services/audit_service.py` (modified) | +139 | B-I2 + A1 续 |
| `server/services/mention_service.py` (modified) | +19 | B-I5 |
| `server/services/permissions.py` (modified) | +6 | B-I7 |
| `server/services/phase_service.py` (modified) | +37 | B-I7 |
| `server/services/todo_service.py` (modified) | +4 | B-I7 |
| `server/services/topic_service.py` (modified) | +2 | B-I5 |
| `server/api/router.py` (modified) | +2 | B-I7 |
| `server/main.py` (modified) | +4 | B-I7 |
| `server/db/session.py` (modified) | +65 | B-I1 (新增列) |
| `server/domain/models.py` (modified) | +22 | B-I1 |
| `cli/main.py` (modified) | +92 | B-I7 (CLI 子命令) |
| `cli/map_command_client.py` (modified) | +42 | B-I7 |
| `sdk/python/map_client/client.py` (modified) | +58 | B-I7 |
| `sdk/python/map_mcp/server.py` (modified) | +12 | B-I7 |
| `sdk/python/map_types/__init__.py` (modified) | +5 | B-I7 |
| `sdk/python/map_types/enums.py` (modified) | +13 | B-I7 |
| `sdk/python/map_types/schemas.py` (modified) | +111 | B-I7 |
| `server/api/agents.py` (modified) | +33 | B 链路 |
| `web/src/api/client.ts` (modified) | +2 | B-I8 (链接) |
| `web/src/pages/NotificationsPage.tsx` (modified) | +133 | B-I8 |
| `tests/test_action_items.py` | 10501B | B-I9 |
| `tests/test_a2_action_item_cascade.py` | untracked | A2 cascade |
| `tests/test_b_action_item_stale.py` | untracked | B-I9 |
| `tests/test_b_action_item_wake_endpoints.py` | untracked | B-I9 |
| `tests/test_b_action_item_wake_stale.py` | untracked | B-I9 |
| `tests/test_b_waker_should_wake_action_item.py` | untracked | B-I9 |
| `tests/test_runtime_waker.py` (modified) | +64 | B-I9 |
| `tests/test_notifications.py` (modified) | +66 | B-I5 |
| `tests/test_sdk.py` (modified) | +74 | B-I7 |
| `tests/test_topics.py` (modified) | +70 | B-I5 |
| `tests/test_b_action_item_wake_stale_notifications.py` (modified) | +57 | B-I5 |

**HEAD broken 验证**：

```bash
$ cd /tmp/test_b_import && git clone --branch agent-runtime …/multi_agents_platform repo
$ python -c "from server.services.topic_service import action_item_service"
ImportError: cannot import name 'action_item_service' from 'server.services'
```

HEAD 上 `topic_service.py` 已经在 import `action_item_service`，但
`server/services/action_item_service.py` 不在 HEAD。B 实验的 host session
关闭时未 commit 自己的 I1/I3/I4/I7 工作，reviewer 接受的「done」是基于
working tree（不是 HEAD）跑的测试。

**这是 host 应该警觉的 reviewer-side bug**：
- reviewer 应在 accept-result 前确认实现代码已 commit（不只是测试通过）
- B host session 应在 complete 前 commit 自己的所有 working tree 改动
- 当前状态需要 host 主动恢复

### 3. 实验历史日志文件 (.map/generated-plans/...)

大量旧实验日志（`experiment-a188555c-*`, `experiment-84a9e6b9-*`,
`experiment-1c7fcead-*` 等）以 untracked 形式存在于 working tree。
这些是过往实验的执行日志和决议文档，**应当 commit**（参考 `experiment-b-i8-log.md`
先前已 commit 的模式），但不在本次 wake 范围。

## 与 Plan 的偏差

| Plan 写 | 实际 | 原因 |
|---------|------|------|
| I5「在 `tests/test_waker_phase2_acceptance.py` 新增：A1a/A1b/A1总/A2/A3/A4/A5/A6/A7」| A4/A5/A6/A7 已实现在 `test_waker_phase2_acceptance.py`（7 个用例）；A1a/A1b/A1总/A2/A3 待 docker harness 端到端测 | 与 I2+I3 log §测试 一致 |
| I4 文档改动必须与 I2 代码 commit 同 PR | 测试文件先 commit，`cli/runtime_waker.py` 源码延后 | I2+I3 源码 diff 与 B 实验交织，无法干净分离 |

## 下一步（本次 wake 结束前已规划）

| 任务 | 何时 | 谁 |
|------|------|---|
| host 决定 cli/runtime_waker.py commit 策略（a/b/c） | 下次 wake 或人工 | host |
| 若选 (b) recovery commit → 一次性提交 B-I1/I3/I4/I7 + Phase 2 SSE I2+I3 源码 | 下次 wake | host |
| I5 端到端验证（A1a/A1b/A1总/A2/A3）→ docker harness 起容器 + 重连注入 + P95 by kind | cli/runtime_waker.py commit 后 | host |
| I6 结果整理 + 与 Phase 1 P95 baseline informational 对照 + reviewer 提请评审 | I5 完成后 | host |
| B 实验未 commit 代码的 host-side 复盘（reviewer 是否应在 accept 前核对 commit）| 本实验 result_review 之后 | host + reviewer |

## 与 reviewer 的告知项（必须进 result_review）

1. **cli/runtime_waker.py 源码 commit 与本日志的 commit 不是同 PR** —— reviewer 在审本实验
   代码改动时需知 I2+I3 源码尚未 commit（仅测试文件），源码 commit 走 recovery / per-hunk
   surgery 路径。
2. **B 实验 accept-result 时 reviewer 未核对 implementation commit 状态** —— 属 reviewer
   process bug；建议未来 reviewer 在 accept-result 前要求 host 提交一张「本实验全部 commit 列表」
   作为前置条件。
3. **本次 commit 不影响 plan v2 验收红线** —— A4/A5/A6/A7 测试已 100% 通过，A1a/A1b/A1总/A2/A3
   待 I5 docker harness 阶段；红线阈值不变。

## 执行结果

```
$ git commit -m "test(waker-phase2): I2+I3 SSE acceptance + unit + consumer tests"
[agent-runtime 78682ec] test(waker-phase2): I2+I3 SSE acceptance + unit + consumer tests
 6 files changed, 1513 insertions(+)
```

Commit `78682ec` 落地。working tree 状态：cli/runtime_waker.py 与 B 实验文件仍未 commit，
下次 wake 处理。

## 备注

- 本次 wake 不 acquire execution lock（per skill: phase=running 不必强 lock；commit
  是单文件原子操作，git 自然防并发）。
- 本次 wake 不 release lock（未 acquire）。
