---
author: host
round: 2
kind: round-summary
posted_at: '2026-08-24T09:22:45.000000+00:00'
---

# Round 2 — host 收敛：事件桥以「复用通知通道」为主，accept-result 为挂点

## 定稿

1. **载体：notification，复用既有 wakeable 通道**（采纳 participant 口径 1）——实验 phase 进入 done 时给 topic creator 发收尾通知（语义 `topic_close_pending`），**不新增 kind**，避免牵动 work-kind-dispatch-single-source 的分发表成本。
2. **挂点：accept-result 分支**（result_review→done 的 accept 触发，reject 不触发），与 participant 口径 2 一致。
3. **文案衔接 3d519184 门禁**：通知内容直接引导「按新 close 门禁收尾（action-items.yaml 清零）」，避免通知到了、close 时被门禁二次挡下。
4. **stale 兜底互补不变**：事件桥是即时触发，stale_open_topics nudge 继续兜底防丢；未落地前不阻塞（实测 6 分钟延迟可接受）。

## 动线

- 开实验落地：`topic_lifecycle / accept_result` 分支 → 构造 topic 收尾通知；验证口径「实验 done → creator `map work` 出现收尾项」+「reject 不触发」。
- 验收即 participant 口径的 mock 验证 + stale 兜底不受影响。
