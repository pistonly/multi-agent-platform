---
title: pending_review 路由死锁：plan 修订后实验从 reviewer 队列消失
status: closed
round: ready
creator: host
created_at: '2026-08-31T06:56:27.309932+00:00'
description: revise plan v2 后 carve-out 误排除，reviewer 不可见永不迁回 running；修路由+回归测试
participants:
- host
- participant
close_reason: experiment_done
close_note: 'decision: 关闭源话题（T8 实验 done，路由死锁修复闭环）

  rationale: 实验 37bfd973 (pending_review 路由死锁修复：carve-out 判定扩展感知 plan_version) phase=done；review_service.py:278
  carve-out plan_version 感知扩展落地；6 case parity + 2 回归护栏；pytest 1875 passed/0 failed；ruff
  0；commit dd837ec 4 文件白名单合规（^server/services/ ^tests/ ^server/）；A7 bd9b21f6 兼容 +
  T1 白名单不动 + review submit 路径不变

  实验: 37bfd973-72ee-4c8a-8b2e-6c19e6d68272

  非阻塞 followups（下次实验同步，不阻塞本话题关闭）: plan A2 (b) 文字笔误、(j) 通知收窄验证缺失、pytest_summary 结构化格式'
---

# pending_review 路由死锁：plan 修订后实验从 reviewer 队列消失

revise plan v2 后 carve-out 误排除，reviewer 不可见永不迁回 running；修路由+回归测试
