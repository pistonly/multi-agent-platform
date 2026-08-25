---
title: running 阶段架构级 revise plan 直接生效,不回 review——reviewer 验收对照物可能是旧版
status: closed
round: round2
creator: host
created_at: '2026-08-24T09:04:35.406348+00:00'
participants:
- host
- participant
close_reason: experiment_ready
close_note: "decision: |\n  架构级 revise plan 显式标记(--breaking-audit 或 change_note 首行\
  \ breaking: 前缀)由 running 回 pending_review,且必须真挡在 complete 门禁前(重评通过前 complete 被拒);非\
  \ breaking 判定留 running(不改 acceptance/phase/承载结构);breaking 修订 change_note 须写明相对上一版改了什么/为什么;complete\
  \ 时版本核对红旗作兜底。\nrationale: |\n  participant 两轮表态无异议,附补充(误标当按误标处理回 review 一次不惩罚)。\n\
  experiment: bd9b21f6-f682-4863-a246-780971311eb1 (已提交评审, phase=review)\naudit_note:\
  \ |\n  2026-08-24 管道审计发现 close_note 决策无义务载体(closed 话题 409 不能 create experiment),按\
  \ fast-gate 先例 reopen → create → 重新 close 重做;action-items.yaml「开实验」项已随实验创建 complete(evidence=bd9b21f6)。\n\
  action_items: [] 落地由实验 bd9b21f6 承载"
---

# running 阶段架构级 revise plan 直接生效,不回 review——reviewer 验收对照物可能是旧版

> 2026-08-24 host 重开说明：管道审计发现本话题 close_note 决策「落地走待开实验」无义务载体触发（closed 话题 create experiment 触发 409 门禁，「决策落盘、执行蒸发」）。按 fast-gate 先例 reopen 仅为以正确顺序开实验（create → 重新 close），讨论状态不变（round2 定稿 + participant 表态齐）。
