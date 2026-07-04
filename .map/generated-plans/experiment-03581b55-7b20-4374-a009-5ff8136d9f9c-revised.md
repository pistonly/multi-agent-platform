# 实验计划 v2：实验执行顺序化与冲突防护机制

## 背景

经过话题 `adf6179d-c57e-4218-a5cb-04c16b22028f`（"执行实验时是否应该顺序执行？并行执行实验修改代码时可能会有冲突"）的两轮讨论，已基本达成共识：当前 MAP 平台在并发执行多个实验时，多个 host worker 可能同时对同一代码仓库进行写操作，导致 git checkpoint 错乱、文件互相覆盖、状态污染等风险。

本实验目标：为 host bridge 增加实验执行门禁与冲突防护机制，确保同一项目同一时刻只有一个实验处于 "executing" 状态，从而避免并行修改代码造成的不可预期后果。

v2 修订要点：依据 reviewer 提出的 7 条 unreasonable items，补齐 TTL/超时耦合、状态机基线确认、迁移 CP、跳过闭环、dry-run 通道、饥饿缓解、压测与回滚 Runbook。

## 目标

1. 在 host bridge / experiment lifecycle 中引入 **per-project 实验执行锁（execution lock）**。
2. 保证同一 `project_id` 下任意时刻最多一个实验处于 `executing` 阶段。
3. 实验进入 `executing` 前先获取锁；完成后（`complete` 或 `failed`）释放锁。
4. 锁等待采用 FIFO 队列 + 超时（默认 600s），超时则上报 `lock_timeout` 事件并回退到 `pending`。
5. 提供 dry-run / 手动 override 通道，便于排障（落地为 `force-release-lock` 子命令 + `MAP_HOST_NO_LOCK` 双开关）。
6. 提供 stale-lock TTL 自愈机制，避免 worker 崩溃后永久死锁。

## 非目标

- 不实现跨项目（多 project_key）调度，本实验仅关心单项目内并发。
- 不实现细粒度文件级冲突检测（仅做粗粒度的"同时只跑一个"）。
- 不改动 reviewer 流程；reviewer bridge 仍按现 auto-resolve 规则运行。

## 范围（in scope）

- `cli/host_worker.py`：在 `execute_experiment` 前后增加锁获取 / 释放；获取失败时按 "跳过闭环策略" 写日志并设置 `next_attempt_at`。
- `cli/experiment_lock.py`（新增）：基于本地 `fcntl.flock` + 服务端 `experiments.status` 双重校验的锁模块；暴露 `acquire / release / is_held / force_release` 接口。
- `cli/experiment_admin.py`（新增）：`map --persona host experiment force-release-lock --id <exp_id>` 子命令，覆盖 dry-run / 手动 override 通道。
- MAP API 侧：在 `experiments` 表新增可空字段 `lock_holder_experiment_id`、`lock_acquired_at`、`lock_ttl_seconds`、`next_attempt_at`、`lock_skip_count`（**仅字段扩展**，是否新增 state 由 CP-0 schema diff 决定）。
- `cli/host_worker.py` 的 `--auto-experiment-lifecycle`：获取锁失败时按 "跳过闭环策略"（见 §跳过策略）处理，不抛异常。
- 测试：单测覆盖获取 / 释放 / 等待 / 超时 / 强制释放 / 跳过回退；集成测覆盖 "N≥4 worker 抢锁只一个胜出" 与 "TTL 自愈"。
- 文档：`docs/PRD-v0.6.md` 更新字段与运维 Runbook。

## 范围外（out of scope）

- Webhook 唤醒机制（参见 `docs/WEBHOOK-TOPIC-HOST.md`，v0.5 不做）。
- 跨仓库 / 多 workspace 调度。
- 性能调优（锁粒度优化等）。
- 真实引入新的 `executing` 中间态（仅当 schema diff 确认当前状态机不存在 `executing` 时才扩展，详见 CP-0）。

## 关键设计决策

### TTL 与锁等待超时的耦合

- `lock_timeout` 默认 600s（外部等待者可等待的最长时间）。
- `lock_ttl_seconds`（服务端防 stale）默认 **1800s**，且 **必须满足** `lock_ttl_seconds ≥ max(lock_timeout, 单次实验 SLA) * 3`。
- 任一参数变更时，运行时 `acquire()` 在 init 阶段做一致性自检：`assert lock_ttl_seconds >= lock_timeout * 3`，不一致直接 fail-fast 并写 ERROR 日志（关键字 `lock_ttl_misconfig`）。
- 文档同步：在 `docs/PRD-v0.6.md` 单独小节 "锁参数耦合表" 列出三者的换算公式。

### 跳过闭环策略（针对 `--auto-experiment-lifecycle`）

- 锁获取失败时：**不抛异常**，不修改 `experiment.status`，但更新以下字段：
  - `next_attempt_at = now() + backoff_seconds`，其中 `backoff_seconds = min(60 * 2^lock_skip_count, 1800)`（指数退避，封顶 30 分钟）。
  - `lock_skip_count += 1`（首次为 1）。
- `--auto-experiment-lifecycle` 轮询时检查 `next_attempt_at`：未到点则跳过；到点则重新入队抢锁。
- 当 `lock_skip_count >= 10` 时上报 `lock_stuck` 事件（host worker 日志关键字 + 可选 webhook，留作未来扩展）。
- 验收增加：`experiments.status` 中可见 `lock_skip_count` 与 `next_attempt_at`，且单测覆盖 backoff 计算。

### FIFO 饥饿缓解（mitigation candidate）

- 在保持 FIFO 主队列的基础上，引入 **优先级老化（aging）**：
  - 实验在等待队列中每 60s 提升一次内部优先级权重 `priority_weight += 1`。
  - worker 选择下一个执行实验时按 `priority_weight DESC, enqueue_at ASC` 排序。
  - 缓解长尾饥饿，但不破坏先来先服务的整体秩序。
- 单元测覆盖：长尾实验等待 3 分钟以上时优先级权重 ≥ 3。
- 备选 mitigation（v3 候选，不在本次实现）：抢占式跳过（高优实验抢占低优）；read-only 降级（饥饿实验降为只读观测）。

### Dry-run / 手动 override 通道

- 新增 CLI：`map --persona host experiment force-release-lock --id <exp_id> [--reason "..."]`
  - 仅允许 host persona 调用；权限复用现有 host token。
  - 操作会写审计日志（关键字 `force_release_lock`，含操作人、reason、时间戳、目标 exp_id）。
  - 若对应 experiment 当前 `status != executing`，命令拒绝并返回 4xx。
- `MAP_HOST_NO_LOCK=1` 环境变量可整体旁路锁机制（用于回滚与历史实验兼容）。
- dry-run 模式：`MAP_HOST_LOCK_DRY_RUN=1` 时，所有 `acquire/release` 只记录日志不真正获取/释放；用于生产排障时验证锁路径。

## 验收标准

1. **功能正确性**
   - [ ] 同时启动两个 host bridge 实例对同一 project 跑实验，仅一个能进入 `executing`；另一个进入等待或被跳过。
   - [ ] 锁在 `complete` / `failed` 后自动释放，第二个实验可继续执行。
   - [ ] 锁等待超时（>600s）后第二个实验收到 `lock_timeout` 并回退。
   - [ ] **（新增）** TTL 自愈：模拟 worker 崩溃不释放，TTL 到期后锁被自动清理，pending 队列恢复。
   - [ ] **（新增）** 跳过闭环：`lock_skip_count >= 1`、`next_attempt_at` 按指数退避正确推进。
   - [ ] **（新增）** 强制释放：`force-release-lock` CLI 成功释放并写审计日志。
2. **可观测性**
   - [ ] `experiments.status` 中可见 `lock_holder_experiment_id` 字段。
   - [ ] host worker 日志包含 `acquire_lock` / `release_lock` / `lock_timeout` / `lock_stuck` / `force_release_lock` / `lock_ttl_misconfig` 关键字。
   - [ ] **（新增）** `lock_skip_count` 与 `next_attempt_at` 暴露在 status 字段。
3. **向后兼容**
   - [ ] `MAP_HOST_NO_LOCK=1` 环境变量可关闭锁机制，便于兼容历史实验。
   - [ ] 不修改 `topic_host` 两轮评论 / Round Summary 行为。
   - [ ] **（新增）** `MAP_HOST_LOCK_DRY_RUN=1` 支持 dry-run 模式。
4. **测试**
   - [ ] 单测覆盖率 ≥ 80%（`cli/experiment_lock.py`、`cli/experiment_admin.py`）。
   - [ ] 集成测试：`tests/integration/test_experiment_lock.py` 通过。
   - [ ] **（新增）** 集成测试：`tests/integration/test_experiment_lock_stress.py` 覆盖 **≥4 worker 并发抢锁**，仅 1 个胜出。
   - [ ] **（新增）** 集成测试：`tests/integration/test_experiment_lock_skip.py` 覆盖指数退避与 `lock_stuck` 触发。

## 实施步骤（带 git checkpoint）

1. **CP-0**：Schema diff 与基线确认。
   - 执行 `map api get /experiments/status --project-id <本项目>`（或读 openapi.json）确认当前状态机是否已含 `executing`。
   - 输出 `docs/exp-lock-schema-diff.md`，明确：(a) `executing` 是否已存在；(b) 待新增字段清单；(c) 是否需要 alembic 迁移。
   - CP：commit `chore(exp-lock): record schema diff baseline`
2. **CP-1**：新建 `cli/experiment_lock.py`，含 `acquire / release / is_held / force_release` 四个核心函数；包含 TTL/超时一致性自检。
   - CP：commit `feat(exp-lock): add experiment_lock skeleton`
3. **CP-2**：在 `cli/host_worker.py` 的 `execute_experiment` 入口调用 `acquire`，出口 / 异常路径调用 `release`；接入跳过闭环（`next_attempt_at` + `lock_skip_count`）。
   - CP：commit `feat(host-worker): integrate experiment lock with skip backoff`
4. **CP-2.5**：新增 `cli/experiment_admin.py` 与 `map experiment force-release-lock` 子命令；审计日志接通。
   - CP：commit `feat(host-cli): add force-release-lock override command`
5. **CP-3**：API 侧扩展 `experiments` 表 schema 与 `experiments.status` 响应字段（`lock_holder_experiment_id`、`lock_acquired_at`、`lock_ttl_seconds`、`next_attempt_at`、`lock_skip_count`）；按 CP-0 结论决定是否扩展状态机。
   - CP：commit `feat(api): expose lock holder + skip fields on experiment status`
6. **CP-3.5**：迁移脚本与回滚 Runbook。
   - 编写 `alembic/versions/<rev>_exp_lock_fields.py`（或等价 SQL 脚本），包含：
     - 新增字段（NULLABLE，default NULL）。
     - 数据迁移：`UPDATE experiments SET status='idle', lock_holder_experiment_id=NULL, lock_acquired_at=NULL WHERE status='executing' AND updated_at < now() - interval '1 hour'`。
   - 编写 `docs/runbooks/exp-lock-rollback.md`：30s 内 `export MAP_HOST_NO_LOCK=1 && systemctl restart host-bridge` 恢复 pending 队列；提供回滚前后对比清单。
   - CP：commit `chore(exp-lock): add migration script and rollback runbook`
7. **CP-4**：补单测 + 集成测（含 stress + skip），更新 `docs/PRD-v0.6.md`（如存在）说明新字段、耦合表与运维 Runbook。
   - CP：commit `test(exp-lock): unit + integration + stress coverage`
8. **CP-5**：reviewer 反馈 → 修订 → approve → start → 完整体跑一次端到端（含 staging 4-worker 并发通过）。
   - CP：commit `chore(exp-lock): e2e verification log`

## 风险

- **死锁风险**：若 worker 在 `executing` 中崩溃且未释放锁，stale lock 清理机制（`lock_ttl_seconds` 默认 1800s = max(lock_timeout, SLA) * 3）兜底。
- **误判风险**：网络分区导致 `release` 失败时双重保护——`fcntl.flock` 在 worker 进程退出时自动释放，服务端状态靠 TTL 自愈；`MAP_HOST_NO_LOCK=1` 可立即旁路。
- **回滚成本**：旧实验 `executing` 状态在迁移时一次性清理（CP-3.5 脚本）；运维通过 `MAP_HOST_NO_LOCK=1` 30s 回滚（CP-3.5 Runbook）。
- **TTL/超时错配**：`lock_ttl_seconds` 必须在 init 时满足 `>= lock_timeout * 3`，否则 fail-fast。
- **FIFO 饥饿**：通过优先级老化缓解，备选 mitigation 列入 v3 backlog。
- **跳过风暴**：指数退避封顶 30 分钟，`lock_skip_count >= 10` 触发 `lock_stuck` 告警，避免日志风暴与真实故障被掩盖。

## 评审关注点（交给 reviewer v2）

- TTL 与锁等待超时的耦合公式（`lock_ttl_seconds >= lock_timeout * 3`）是否合理。
- 优先级老化（aging）作为 FIFO 饥饿缓解是否充分，或需引入抢占式策略。
- 跳过闭环中的 `lock_skip_count >= 10` 阈值与 backoff 封顶 1800s 是否合适。
- 强制释放 CLI 的权限模型是否需要额外约束（如双人复核）。

## 完成定义（Definition of Done）

- 上述 4 条验收标准全部勾选（含新增子项）。
- reviewer 已 approve，实验 `complete`。
- 文档同步：`docs/PRD-v0.6.md`（或同档位文档）、`docs/runbooks/exp-lock-rollback.md`、`docs/exp-lock-schema-diff.md` 均已落地。
- **（新增）** staging 环境跑过 ≥4 worker 并发抢锁的 stress test 通过。
- **（新增）** 已实测 `MAP_HOST_NO_LOCK=1` 回滚 Runbook 能在 30s 内恢复 pending 队列。
- **（新增）** 至少一次真实双 worker 并发跑实验成功（兼容原 DoD 第 4 条）。
- **（新增）** 迁移脚本在 staging 演练过，输出 `pre / post` 实验状态对比。
