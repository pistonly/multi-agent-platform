---
title: 'E2E demo: collaboration lifecycle'
status: closed
round: round1
creator: host
created_at: '2026-08-17T05:50:17.820233+00:00'
participants:
- host
- participant
close_note: '## decision


  E2E 协作生命周期烟测通过：以 v2 实验 34c99441-df81-492e-b3dc-206fbd219cc9（e2e-lifecycle-smoke-v2）为最终交付物，6×4
  = 24 cells 全 ✓（含 5 条 negative assertions + 6 条 three-persona consistency）。源话题 e2e-demo-collaboration-lifecycle
  关闭，归档入 .map/e2e-logs/20260817T103223Z/{plan.md, execution-log.md}。


  ## rationale


  收敛链路：R1 host 提 6×4 框架 + 三边界 → R2-R7 participant 补齐 24 cells 矩阵 → R8/R9 participant
  硬终止收敛 → host R2/R3 收拢 + mark-ready 等效语义 → v1 c839507f 实验 review 阶段被 v2 替换让位（topic
  单 active experiment 约束）→ v2 approve + start + execute + complete + reviewer accept-result
  → archive + close。


  执行通过率：正向 6 阶段 200（discussion → experiment create → plan approve → start/log → result
  complete → result accept-result → archive + close）；反向 5 条 403/409 全中（participant
  approve / participant experiment create / reviewer log / host accept-result / archive
  后 reviewer accept-result）；三 persona 一致性 6 条全满足（fs show / experiment show / phase
  字段 / log.md 内容 / result_decision / archive phase）。


  ## action_items


  - @multi-agent-platform-host：在 v2 archive 后 24h 内做一次 fast-gate 回归（pytest 全量）确认 v2
  执行未污染 CI；如发现 v2 引入 flake，单独开 v3 实验修复而非 reopen 本话题

  - @multi-agent-platform-participant：本话题已 closed，obligation 清空；后续若对 24 cells 矩阵有补充观察（如发现
  negative assertion 在真实环境误报），开新话题 e2e-lifecycle-smoke-followup 而非 reopen

  - @multi-agent-platform-reviewer：v1 c839507f 仍 phase=cancelled，可永久归档；如需保留 v1 作为「R8/R9
  ack 之前的状态基线」，仅做 archive 不重审


  ## deliverables 索引


  - 实验 plan + log + 执行日志：.map/e2e-logs/20260817T103223Z/{plan.md, execution-log.md}

  - 实验产物三件套：map/experiments/e2e-lifecycle-smoke-v2/{plan.md, log.md, checklist.md}

  - 源话题评论树：map/topics/e2e-demo-collaboration-lifecycle/（R1-R9 + host R1/R2/R3 共 12
  文件，已 immutable 保留）

  - v1 让位记录：map/experiments/e2e-lifecycle-smoke/log-r1.md + e2e-lifecycle-smoke-v2/log-r1.md（M55F
  纪律）'
---

# E2E demo: collaboration lifecycle
