# 实验计划：实验执行顺序化与冲突防护机制

## 背景

经过话题 `adf6179d-c57e-4218-a5cb-04c16b22028f`（"执行实验时是否应该顺序执行？并行执行实验修改代码时可能会有冲突"）的两轮讨论，已基本达成共识：当前 MAP 平台在并发执行多个实验时，多个 host worker 可能同时对同一代码仓库进行写操作，导致 git checkpoint 错乱、文件互相覆盖、状态污染等风险。

本实验目标：为 host bridge 增加实验执行门禁与冲突防护机制，确保同一项目同一时刻只有一个实验处于 "executing" 状态，从而避免并行修改代码造成的不可预期后果。

## 目标

1. 在 host bridge / experiment lifecycle 中引入 **per-project 实验执行锁（execution lock）**。
2. 保证同一 `project_id` 下任意时刻最多一个实验处于 `executing` 阶段。
3. 实验进入 `executing` 前先获取锁；完成后（`complete` 或 `failed`）释放锁。
4. 锁等待采用 FIFO 队列 + 超时（默认 600s），超时则上报 `lock_timeout` 事件并回退到 `pending`。
5. 提供 dry-run / 手动 override 通道，便于排障。

## 非目标

- 不实现跨项目（多 project_key）调度，本实验仅关心单项目内并发。
- 不实现细粒度文件级冲突检测（仅做粗粒度的"同时只跑一个"）。
- 不改动 reviewer 流程；reviewer bridge 仍按现 auto-resolve 规则运行。

## 范围（in scope）

- `cli/host_worker.py`：在 `execute_experiment` 前后增加锁获取 / 释放。
- `cli/experiment_lock.py`（新增）：基于本地 `fcntl.flock` + 服务端 `experiments.status` 双重校验的锁模块。
- MAP API 侧：扩展 `experiments.status` 状态机，新增 `executing` 中间态的可观测字段 `lock_holder_experiment_id`。
- `cli/host_worker.py` 的 `--auto-experiment-lifecycle`：获取锁失败时跳过当前实验并写日志，不抛异常。
- 测试：单测覆盖获取 / 释放 / 等待 / 超时；集成测覆盖 "两个 worker 抢锁只一个胜出"。

## 范围外（out of scope）

- Webhook 唤醒机制（参见 `docs/WEBHOOK-TOPIC-HOST.md`，v0.5 不做）。
- 跨仓库 / 多 workspace 调度。
- 性能调优（锁粒度优化等）。

## 验收标准

1. **功能正确性**
   - [ ] 同时启动两个 host bridge 实例对同一 project 跑实验，仅一个能进入 `executing`；另一个进入等待或被跳过。
   - [ ] 锁在 `complete` / `failed` 后自动释放，第二个实验可继续执行。
   - [ ] 锁等待超时（>600s）后第二个实验收到 `lock_timeout` 并回退。
2. **可观测性**
   - [ ] `experiments.status` 中可见 `lock_holder_experiment_id` 字段。
   - [ ] host worker 日志包含 `acquire_lock` / `release_lock` / `lock_timeout` 关键字。
3. **向后兼容**
   - [ ] `MAP_HOST_NO_LOCK=1` 环境变量可关闭锁机制，便于兼容历史实验。
   - [ ] 不修改 `topic_host` 两轮评论 / Round Summary 行为。
4. **测试**
   - [ ] 单测覆盖率 ≥ 80%（`cli/experiment_lock.py`）。
   - [ ] 集成测试：`tests/integration/test_experiment_lock.py` 通过。

## 实施步骤（带 git checkpoint）

1. **CP-1**：新建 `cli/experiment_lock.py`，含 `acquire(project_id, exp_id, timeout)` / `release()` / `is_held()` 三个核心函数。
   - CP：commit `feat(exp-lock): add experiment_lock skeleton`
2. **CP-2**：在 `cli/host_worker.py` 的 `execute_experiment` 入口调用 `acquire`，出口 / 异常路径调用 `release`。
   - CP：commit `feat(host-worker): integrate experiment lock`
3. **CP-3**：API 侧扩展 `experiments.status` 的 schema，添加 `lock_holder_experiment_id` 可空字段。
   - CP：commit `feat(api): expose lock holder on experiment status`
4. **CP-4**：补单测 + 集成测，更新 `docs/PRD-v0.6.md`（如存在）说明新字段。
   - CP：commit `test(exp-lock): unit + integration coverage`
5. **CP-5**：reviewer 反馈 → 修订 → approve → start → 完整体跑一次端到端。

## 风险

- **死锁风险**：若 worker 在 `executing` 中崩溃且未释放锁，需要 stale lock 清理机制（TTL 默认 1800s）。
- **误判风险**：网络分区导致 `release` 失败时双重保护——`fcntl.flock` 在 worker 进程退出时自动释放，服务端状态靠 TTL 自愈。
- **回退成本**：旧实验 `executing` 状态在迁移时需一次性迁移脚本将所有非锁定中的实验标记为 `idle`。

## 评审关注点（交给 reviewer）

- 锁粒度是否过粗（仅 project 级 vs 仓库级 vs 文件级）。
- TTL 默认值 1800s 是否合理。
- FIFO 队列是否有 starvation 风险。

## 完成定义（Definition of Done）

- 上述 4 条验收标准全部勾选。
- reviewer 已 approve，实验 `complete`。
- 文档同步：`docs/PRD-v0.6.md`（或同档位文档）已更新。
- 至少一次真实双 worker 并发跑实验成功。
