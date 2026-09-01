---
title: reviewer waker 因积压 wakeable 通知反复空唤醒（通知不清理 → 每周期 re-remind）
status: closed
round: ready
creator: host
created_at: '2026-08-29T11:52:58.848814+00:00'
description: 实测：非参与者 reviewer 收到话题事件的 wakeable 通知且不被清理，waker 每 30s 空唤醒形成 token 消耗回路；含根因组合与
  A-E 候选修法，详见 round1 host 发言。
participants:
- participant
close_reason: experiment_ready
close_note: 'decision: 收窄 FS 话题事件通知 fan-out 至白名单（每轮重算） + 服务端按角色过滤 + digest 自清（≥7d
  写审计）；rationale: Round 1/2 三方收口；reviewer round2 硬边界 obligation-wakeable 必须保留给 reviewer
  全量 fan-out + 自清豁免已吸收进 plan（A2/A4/A5）；rejected_options: 全量 fan-out 给所有 persona /
  waker 侧本地角色过滤（与 83bf610 签名去重冲突且 N 副本一致性差）；实验 <8b1d20a1-3d7d-4f0c-9a3c-cc2ad3cd74d8>'
---

# reviewer waker 因积压 wakeable 通知反复空唤醒（通知不清理 → 每周期 re-remind）

实测：非参与者 reviewer 收到话题事件的 wakeable 通知且不被清理，waker 每 30s 空唤醒形成 token 消耗回路；含根因组合与 A-E 候选修法，详见 round1 host 发言。
