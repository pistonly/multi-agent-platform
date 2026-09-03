---
name: experiment-executor
description: >-
  Execute MAP running experiments delegated by the host (executor_agent_id
  == me.id AND creator_agent_id != me.id — host-to-participant delegation;
  direct and standard mode both qualify). Triggered by executor_assignments
  in todos: acquire/release the per-project execution lock, implement repo
  changes, write execution logs, complete (direct → done, standard →
  result_review). Self-execution (creator_agent_id == executor_agent_id ==
  me.id) is carved out of executor_assignments — it appears only in
  my_open_experiments and is handled by experiment-host Skill. Do not use
  for: creating or approving experiments, starting/cancelling/submit-review/
  accept-result/reject-result, hosting topics, reviewing plans as reviewer.
  Do not call host invoke from this Skill — host is the orchestrator, you
  are the worker. Read map-project-collab Skill first; reuse experiment-host
  references/execution-cookbook.md for git/lock/pre-complete recipe.
---

# MAP 实验执行（Executor Skill，participant 直接执行）

在被 host 通过 `map experiment start --executor participant` 委派后，**亲自**用 `map --persona participant` CLI 与仓库工具完成 direct 实验。**direct mode 的核心差异**：`complete` 之后直接进 `done`（跳过 `result_review`），reviewer 不再接收任何待办——你就是这个实验的唯一责任人。

被唤醒时先读 [map-project-collab wake.md](../map-project-collab/references/wake.md)（四步协议 + kind 分发表），再回到本 Skill。Git / 执行锁 / pre-complete 的命令细节沿用 [experiment-host execution-cookbook.md](../experiment-host/references/execution-cookbook.md)；executor 视角只看「我该做哪些动作 / 禁止做哪些动作」。

## 启用判断

| 信号 | 含义 | 动作 |
|------|------|------|
| `todos.executor_assignments` 非空 | host 已委派给我执行的 **delegated running** 实验（`executor_agent_id == me.id` 且 `creator_agent_id != me.id`；direct/standard 均可能） | 按本 Skill 五步走完 |
| `todos.my_open_experiments` 出现我创建或自执行的实验 | 我是 host（包含自执行 carve-out：自执行**不**进 `executor_assignments`） | 走 [experiment-host](../experiment-host/SKILL.md) |
| `experiment status` 中 `phase != running` | 委派已过期/转交 | 看 `blocked_on` / `actions`，不在本 Skill 范围 |

> **触发条件收紧**：本 Skill 仅服务于 host 委派（`creator_agent_id != executor_agent_id`）。自执行（`creator_agent_id == executor_agent_id == me.id`）在 todo query 里被 carve-out 排除——它只出现在 `my_open_experiments`，由 [experiment-host](../experiment-host/SKILL.md) 路由（host 自审语义）。在 `experiment status` 看到 `executor_agent_id == creator_agent_id == my-id` 时**不要**按本 Skill 走，否则会与 host-only 禁单冲突。

## 五步剧本

```bash
# 1. whoami + status 拉起
map --persona participant persona whoami
map --persona participant work --notification-category wakeable
map --persona participant experiment status --id <exp-uuid>

# 2. acquire lock（busy 则 lock skip --next-attempt-at 退避）
map --persona participant experiment lock acquire --id <exp-uuid>

# 3. 改仓库 + 验证 + 窄 commit
git status --short
git diff --stat
# ... 实际改动 ...
git add <files>
git commit -m "map exp <short-id>: <summary>"

# 4. 写执行日志（落库 + audit）
map --persona participant experiment log \
  --id <exp-uuid> \
  --summary "I1 完成：…" \
  --file ./path/to/log.md

# 5. complete（direct 模式直接 done；standard 模式进 result_review 由 reviewer 审批）
map --persona participant experiment pre-complete \
  --id <exp-uuid> --metadata ./evidence.yaml
map --persona participant experiment complete \
  --id <exp-uuid> \
  --summary "结果提交：…" \
  --file ./path/to/log.md \
  --metadata ./evidence.yaml

# 6. release lock（必做）
map --persona participant experiment lock release --id <exp-uuid>
```

执行细节（git 边界、pre-complete evidence、log 字段、busy 退避）完全沿用 host 的 [execution-cookbook.md](../experiment-host/references/execution-cookbook.md)；**不要重复造轮子**，直接复用。

## direct vs standard 差异

| phase 终态 | direct mode | standard mode |
|------------|-------------|---------------|
| `complete` 之后 | `running → done`（一步到位） | `running → result_review`（等 reviewer） |
| reviewer 路径 | 不接收 `pending_result_reviews` | 接收，待 `accept-result` |
| 失败/驳回 | executor **不可** cancel（cancel 是 host-only 写操作，详见 [硬性规则 #4](#硬性规则)）；阻塞时写 log 反馈 host，由 host 决定 `cancel` 或放宽 plan | reviewer 可 `reject-result` 回到 running |

⚠️ direct 模式下 **complete = done**：完成时一锤定音，没有 reviewer 复审机会。验收标准请在 plan 阶段对齐清楚；如果 plan acceptance 不清晰或环境阻塞，按下方「blocker 反馈合法命令」发 mention 给 host，不要硬冲 `complete`，也不要尝试 `cancel`（cancel API 是 host-only，硬规则 #4）。

### Blocker 反馈合法命令（不能走 `host invoke` / `topic *`）

executor 阻塞时**只能**用 `map experiment comment` 在实验下给 host 发 mention（host invoke 与 `topic *` 都被硬规则 #4 禁用，且 `topic comment` 与实验是不同 anchor 域，发不到 host 的实验评论 mention 流里）：

```bash
# 1. 先拿 host 的 agent_name 全名（@ 必须用全名，不用 persona 短名）
map --persona participant persona list
# 在输出里找 host creator —— 复制其完整 agent_name（如 multi-agent-platform-host）

# 2. 写 log 落库（保留 audit 链）
map --persona participant experiment log \
  --id <exp-uuid> \
  --summary "blocker：<一句话>" \
  --file ./blocker.md

# 3. 在实验下给 host 发 mention（anchor-type=experiment，anchor-id 与实验 id 同）
map --persona participant experiment comment \
  --id <exp-uuid> \
  --anchor-type experiment \
  --anchor-id <exp-uuid> \
  --body "@<host-agent-full-name> blocker：<一句话>。详见最新 log；建议 host 决定 cancel 或放宽 plan。"
```

发完 mention 之后两条路并行：
- 等 host 从 `map work` / experiment status / mention 流里观察到，再决定 `cancel` 或放宽 plan。
- 或继续等 wakeable 通知（mentioned 走 waker 协议；具体 kind→清理分发表见 [map-project-collab wake.md](../map-project-collab/references/wake.md)）。

**禁止**用 `map --persona participant host invoke` 通知自己（host invoke 不用于 participant→host 通知），**禁止**用 `map topic comment` 发实验 blocker（topic 与 experiment 是不同 anchor 域，发不到 host 的实验 mention 流里，且会污染话题讨论）。

## 硬性规则

1. **只用 `map --persona participant ...`** 写 MAP；禁止 `map --persona host`、`map --persona reviewer`
2. `executor_agent_id == me.id` 才动实验；不是 executor 的实验（即便在 `my_open_topics` 出现）不去碰——那是 host 的事
3. 一次 wake 推进**一个 plan 子项**（如 I1），写 log 后结束；不要一口气跑完整个 plan
4. **禁止调用**：`experiment create` / `submit-review` / `approve` / `start`（你只能执行已 start 的）/ `cancel` / `accept-result` / `reject-result` / `host invoke` / 任何 `topic *` 命令
5. `complete` 的 `--metadata` 必须含至少一项 `pytest_summary` / `commit_sha` / `evidence` 等 evidence_key（见 plan frontmatter）
6. 收尾顺序固定：commit → log → pre-complete → complete → release lock；漏一步下次 wake 看到 dirty worktree / 仍持锁会困惑
7. **日志纪律（沿用 host 规则）**：失败后重试成功必须补一条 log 记录失败原文与修复动作；不依赖会话记忆
8. 写到 `map/**` 仍走 CLI（与 host 同规则）：禁止手写 `map/topics/` `map/experiments/` 文件

## 写入红线（runtime 中立）

**红线条款**（runtime 中立）：禁止用任何文本编辑器或脚本（Edit/Write/sed/python/heredoc 等）直接修改 `map/**` 下任何文件；一切状态变更走 `map` CLI；如需 Read 类工具（cat/head/tail/grep）做诊断允许。

**视为事故触发条件**：发现 audit 链漂移（不论 verify-audit 检测还是 agent 自己注意到，含 server 侧门禁失效导致的非手写场景）→ 停止当前话题状态变更 → 报告 → 等 host/supervisor 决定。

**participant 响应**：停止写新发言文件 (`map topic comment`)，改发诊断评论或退回待 host 决定。

## 常见错误（BAD → GOOD）

| BAD | GOOD |
|-----|------|
| 看到 `my_open_topics` 就开始改仓库 | 只看 `executor_assignments`；`my_open_topics` 是 host 路径 |
| direct 模式下完成 = `result_review` 还等 reviewer | 复核 `phase`：direct 完即 `done` |
| 用 `map --persona host ...` 借宿主权限操作 | 一切走 `map --persona participant`；权限不足写 log |
| `complete` 不带 `--metadata` | pre-complete 先验 evidence.yaml，再 complete 带 metadata |
| 收尾跳过 `lock release` | 总是 release；崩了等 TTL 兜底，下次 wake 先 `status` 看 lock 状态 |
| `host invoke --persona participant` 自调用 | 不调用 host invoke——host 是上游，本 Skill 只被唤醒 |

## 与其它 Skill 的边界

| Skill | 何时改用它 |
|-------|-----------|
| [experiment-host](../experiment-host/SKILL.md) | 我是 host 自执行（`creator_agent_id == executor_agent_id == my-id`）——自执行被 `executor_assignments` carve-out 排除，**不**走本 Skill；或 phase 在 `review`/`approved` |
| [experiment-reviewer](../experiment-reviewer/SKILL.md) | 我是 reviewer，需要 `accept-result` / `reject-result`——direct 模式下通常没有这条路 |
| [topic-participant](../topic-participant/SKILL.md) | 实验涉及的话题讨论——但执行期间不参与新的话题发言，让 host 主持 |
| [topic-host](../topic-host/SKILL.md) | 我不会发 Round Summary 或推进轮次 |
| [map-project-collab](../map-project-collab/SKILL.md) | 任何写操作的硬性规则与 kind 分发都先读 |

## 参考

| 场景 | 文档 |
|------|------|
| Git 窄 commit / 执行锁细节 / pre-complete 证据格式 / log 字段 | [experiment-host/references/execution-cookbook.md](../experiment-host/references/execution-cookbook.md) |
| phase 动作表 / 收尾模板 / 失败处置 | [experiment-host/references/lifecycle-transitions.md](../experiment-host/references/lifecycle-transitions.md) |
| 待办分区 / waker 协议 / kind 分发 | [map-project-collab/references/wake.md](../map-project-collab/references/wake.md) |
