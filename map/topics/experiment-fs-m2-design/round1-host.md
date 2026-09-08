---
author: host
round: 1
kind: user
posted_at: '2026-09-04T16:15:57.033661+00:00'
---

## 讨论目标

实验 FS 化 M1 已建立 map/experiments/<slug>/index.md 契约；本话题讨论 M2：如何让实验生命周期逐步收敛到 FS 事实源，并安全停止不必要的 DB INSERT。

## 请重点讨论

1. 事实源边界：index.md、plan.md、log.md、review.yaml 与 DB 各自保留哪些字段，最终状态以谁为准。
2. 对账机制：map experiment sync --check 的 diff 范围、输出格式、零 diff 判定和失败处理。
3. 切换策略：何时停止 DB INSERT，是否需要 feature flag、双写观察期或兼容窗口。
4. 远程部署：projection-cache、同步发布、并发修改和 revision/CAS 如何与实验状态机结合。
5. 迁移与回滚：已有 DB 实验如何迁移，部分同步、删除、重命名或进程中断时如何恢复。
6. 验收标准：需要哪些单测、远程与本地 e2e、并发、断网恢复和升级测试。

## 初步倾向

建议先完成只读对账与告警，再分阶段切换写入；在事实源、冲突策略和回滚路径未明确前，不直接进入实现实验。请 participant 重点指出该方案的风险、遗漏和更简单的替代方案。
