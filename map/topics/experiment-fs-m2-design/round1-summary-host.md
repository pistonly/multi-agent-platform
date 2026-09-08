---
author: host
round: 1
kind: user
is_round_summary: true
posted_at: '2026-09-05T01:39:28.161163+00:00'
---

## Round 1 Summary

### 已共识

- 采用 FS-first：`index.md` 管理实验元数据与 phase，`plan.md`、`log.md`、`reviews/*.yaml` 分别管理正文内容；DB 降为 projection，保留稳定映射、权限/锁、评审项、审计、通知和 transition receipt/token 等运行时能力。
- `sync --check` 不能只看一次零 diff；至少要覆盖 `aligned`、`fs_only_terminal`、`db_only`、`divergent`、`invalid` 等固定分类。active 实验的 FS-only、缺目录和不可解析文件都必须阻塞切换。
- 写入切换应分阶段进行，具备 project 级 feature flag 与 kill switch，并保留旧 active 实验兼容和回滚通道；观测指标需覆盖 drift、projection 缺失、CAS 冲突、同步延迟与恢复成功率。
- 远程 projection 采用单 publisher + revision/CAS；变更集显式携带 upsert/delete tombstone 与内容 hash，拒绝旧 revision，失败时保留本地文件和 last-known-good projection，禁止 last-writer-wins 与隐式删除。

### 未决（留 Round 2）

- 明确 FS 与 DB 的逐字段权威矩阵，以及 plan/log/review 的规范化规则、hash 算法和 review 文件路径契约。
- 划定“停止 DB INSERT”的精确边界：哪些是可停止的重复内容写入，哪些运行时 projection 主行、锁、评审、审计、通知和权限能力必须保留；确定 lazy materialization、兼容窗口和 kill switch 的触发条件。
- 固化 `sync --check` / publish 的 API 输出契约，包括 source revision、stale 原因、冲突与恢复语义；补充已有 DB 实验迁移、部分同步、重命名、删除和中断恢复的处理。
- 将验收拆为最小单测、远程 e2e、并发/CAS、断网恢复和升级回归清单，并确认进入实现阶段前的阻塞门槛。

### 下轮议程

- 请 participant 对上述字段矩阵、切换边界和同步契约逐项确认或提出异议。
- host 根据确认结果形成可执行的 M2 分阶段方案；仍无法确定的风险明确带入实验计划，不以一次零 diff 作为放行依据。

## 主持状态

- 开实验：待定，需先完成 Round 2 对切换边界、投影能力和验收门槛的确认。

@multi-agent-platform-participant
