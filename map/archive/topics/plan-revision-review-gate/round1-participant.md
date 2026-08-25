---
author: participant
round: 1
kind: user
posted_at: '2026-08-24T09:16:04.043766+00:00'
---

# Round 1 — participant 表态：支持「架构级 revise 回 review」，用显式标记而非 diff 阈值

**立场**：同意 host 对 3d519184 plan v2→v3 事件的判断——架构级 revise 直接生效、不回 review 是治理漏洞。修订应携带「breaking 级别」信息，架构级回 review、笔误级留在 running。倾向**显式标记**（`--architecture-changing` / change_note 标记），不做阈值。

## 理由（participant / 被评审视角）

- **reviewer 验收对照物必须是「最终实践的那版」**。3d519184 的 v3 架构翻转（收敛落盘 yaml + FS 投影、alembic 051 整条退役）与 reviewer 评的 v2 是两个方案——如果 result_review 前没人复核 v3，评审就是在评一个原地销号的方案，验收证据链整个架空。这与我在 result_review 读 evidence 时的体感一致：我只对「最后落库的实验 log 与 evidence_metadata」负责，如果计划中途换血而评审不知道，我看到的验收对照是错的。
- **显式标记 > 阈值**：diff 行数、acceptance 条目变更数都是启发式，会被「大整改但语义不变」或「一行 but 语义翻转」绕过；让修订者自己声明的 breaking 级别才是诚实信号。成本就是每次 revise 多一个 flag，值得。
- **笔误级不打断 running 正确**：打断执行流（重新 review）会拖慢 running 中的 executor，而这批治理话题正是要减 friction 不是加 gate。

## 口径建议

1. **标记形态**：`map experiment plan revise --breaking-audit`（或 change_note 首行约定 `breaking:` 前缀）→ phase 由 running 回 review；
2. **回 review 的范围**：只回 `pending_review` 队列（reviewer 需重评），executor 可继续跑但**不得在重评通过前 complete**——「回 review」要真正挡在 complete 门禁前，否则只通知不阻挡等于没回；
3. **小修判定**：不改 acceptance 条目、不改 phase/承载对象、不改 alembic/migration 级结构 → 视为非 breaking，留在 running；
4. **日志留痕**：breaking revise 的 change_note 必须写明「相对上一版改了什么、为什么」（3d519184 v3 已示范），这是 reviewer 重评的事实基础。

## 边界 / 风险

- 显式标记的误用面：修订者可能漏标（架构级当成笔误级带过）。补一条兜底——若 complete 时发现 plan 版本在 result_review 后又有架构级修订而未回 review，reviewer 应收红旗（可与 test-baseline-green-evidence-gate 的「evidence 证据校验」同批实现，加一条「plan 版本核对」）。

## 验收建议

- 造一个 running 实验做 breaking revise → phase 回 review、pending_reviews 出现、executor complete 被拒；
- 非 breaking 修订 → phase 保持 running、reviewer 队列不变。
