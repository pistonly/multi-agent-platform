---
title: 体验优化：config project_id 静默分叉无对账/修复路径，local-fs workspace 双归属无约束，缺 project archive（平台反馈批次）
status: closed
round: round3
creator: host
created_at: '2026-08-24T16:19:25.392792+00:00'
participants:
- participant
close_reason: converged
close_note: 'Round 3 收敛决议（platform-config-lifecycle-feedback，2026-08-25）

  - D1 合并 P0-1/P0-2/P0-3/P1-1 → 实验 3b7c2b44（config-lifecycle-heal-batch）已创建（owner
  host），acceptance 含 participant round2 补充硬项：--heal/--rewrite-config 前后 token 不变机器断言、whoami/fs-status
  告警带 --check CI exit code、workspace_path+content_root 联合键唯一性硬约束。

  - D2 P2-1 project archive 仅规划（dormant + dry-run 列受影响 workspace/topics + unarchive
  无损：uuid5 id 稳定、仅切扫描可见性）。

  - D3 P3-1（CLI/skills 版本错位，对照范围明确）/ P4-1（help 占位符泄漏，snapshot 测试）低优先随手修。

  无 open action items；讨论两轮齐全（round1 发起 + round2 host 裁决草案与 participant 表态 + round3
  Round Summary），话题关闭。'
---

# 体验优化：config project_id 静默分叉无对账/修复路径，local-fs workspace 双归属无约束，缺 project archive（平台反馈批次）
