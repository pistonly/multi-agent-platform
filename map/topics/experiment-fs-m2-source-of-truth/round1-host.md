---
author: host
round: 1
kind: user
posted_at: '2026-09-04T16:14:56.985197+00:00'
---

## 讨论目标\n\n实验 FS 化 M1 已建立  契约；本话题讨论 M2：如何让实验生命周期逐步收敛到 FS 事实源，并安全停止不必要的 DB INSERT。\n\n## 请重点讨论\n\n1. **事实源边界**：、、、 与 DB 各自保留哪些字段，最终状态以谁为准。\n2. **对账机制**：ok: True
matched: 4
diffs: 0
repairs: 0
fs_only: 48
db_only: 0
missing_dir: 0
authority: index.md after validated write 的 diff 范围、输出格式、零 diff 判定和失败处理。\n3. **切换策略**：何时停止 DB INSERT，是否需要 feature flag、双写观察期或兼容窗口。\n4. **远程部署**：projection-cache、同步发布、并发修改和 revision/CAS 如何与实验状态机结合。\n5. **迁移与回滚**：已有 DB 实验如何迁移，部分同步、删除、重命名或进程中断时如何恢复。\n6. **验收标准**：需要哪些单测、远程/本地 e2e、并发、断网恢复和升级测试。\n\n## 初步倾向\n\n建议先完成只读对账与告警，再分阶段切换写入；在事实源、冲突策略和回滚路径未明确前，不直接进入实现实验。请 participant 重点指出该方案的风险、遗漏和更简单的替代方案。
