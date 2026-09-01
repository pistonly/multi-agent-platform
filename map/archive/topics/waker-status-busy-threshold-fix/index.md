---
title: waker status busy 档口径修正：expected_remind_runtime 真实同源
status: closed
round: ready
creator: host
created_at: '2026-08-31T09:24:35.348422+00:00'
description: busy 超 180s 误报 stale/dead；fallback 偷换语义未与 server 30min 容忍同源；state.json
  序列化+同帧测试+验收模板硬约束
participants:
- host
- participant
close_reason: experiment_ready
close_note: 'decision: 关闭源话题（T9 实验已建 + submit-review，进入 reviewer 评审）

  rationale: Round 1 共识收口（host T9 任务书 + participant §1-§8 表态），议题完全收敛：根因 cli/waker_status_view.py:154
  busy_stale fallback 偷换 idle_stale_w 语义（180s vs 1800s 是 10× 口径差，P0）；任务三件套（state.json
  序列化 + fixture-based 同帧测试 + 验收模板二段式）+ participant §3 atomic write + §4 schema 注释
  + §6 pid zombie 排除全部采纳为主线任务 I1-I8；3-waker smoke 覆盖（host 真实 + participant/reviewer
  fixture）§5 host 已答复。Round 1 Summary 内容融入 plan.md background 段（CLI --force 误覆盖事故：独立
  round1-summary-host.md 路径被 CLI 解析为覆盖 round1-host.md，已 Write 恢复 round1-host.md 原文
  + orphan 文件作为事故痕迹保留）

  实验: d12c328c-46ad-4ce7-8440-27fc52d7c8a8

  任务书: map/experiments/waker-status-busy-threshold-fix/plan.md

  验收模板二段式落点: .cursor/skills/experiment-host/SKILL.md 视图类实验 checklist（避免 T6 busy 档漏核教训重演）

  action_items 收口（result_review 阶段）: 监督者重启 server + waker 生效（daemon restart，无 docker
  build），避免验收通过却未生效'
---

# waker status busy 档口径修正：expected_remind_runtime 真实同源

busy 超 180s 误报 stale/dead；fallback 偷换语义未与 server 30min 容忍同源；state.json 序列化+同帧测试+验收模板硬约束
