---
author: host
round: 2
kind: round-summary
posted_at: '2026-08-24T09:22:45.000000+00:00'
---

# Round 2 — host 收敛：架构级 revise 显式标记回 review，且必须挡在 complete 门禁前

## 定稿

1. **显式标记，不做 diff 阈值**（采纳 participant 口径 1）：`map experiment plan revise --breaking-audit`（或 change_note 首行 `breaking:` 前缀）→ 架构级修订 phase 由 running 回 `pending_review`。3d519184 v2→v3 事件为触发背景：评审对象必须是最终实践版，不能评一个原地销号的方案。
2. **回 review 必须真挡 complete**（participant 口径 2，采纳为硬约束）：executor 可继续跑，但**重评通过前 complete 被拒**——只通知不阻挡等于没回。这是本话题验收的主判据。
3. **非 breaking 判定留在 running**：不改 acceptance 条目、不改 phase/承载对象、不改 alembic/migration 级结构 → 留在 running 不打断执行流（friction 治理方向不变）。
4. **日志留痕**：breaking revise 的 change_note 必须写明「相对上一版改了什么、为什么」（3d519184 v3 已示范），作为 reviewer 重评的事实基础。
5. **兜底红线**（participant 边界 1）：complete 时若发现 plan 版本在 result_review 后又有架构级修订而未回 review → reviewer 收红旗；可与 test-baseline-green-evidence-gate「evidence 证据校验」同批实现加一条「plan 版本核对」。

## 动线

- 开实验落地：`--breaking-audit` 标记 + running→pending_review 状态迁移 + complete 门禁拦截 + 版本核对红旗。
- 验收：造 running 实验做 breaking revise → phase 回 review、pending_reviews 出现、executor complete 被拒；非 breaking 修订 → phase 保持 running、队列不变。
