---
title: running 阶段架构级 revise plan 直接生效,不回 review——reviewer 验收对照物可能是旧版
status: closed
round: round2
creator: host
created_at: '2026-08-24T09:04:35.406348+00:00'
participants:
- host
- participant
close_reason: concluded
close_note: 'decision: 架构级 revise plan 显式标记(--breaking-audit 或 change_note 首行 breaking:
  前缀)由 running 回 pending_review,且必须真挡在 complete 门禁前(重评通过前 complete 被拒);非 breaking
  判定留 running(不改 acceptance/phase/承载结构);breaking 修订 change_note 须写明相对上一版改了什么/为什么;complete
  时版本核对红旗作兜底(可随 test-baseline-green-evidence-gate 同批);rationale: participant 两轮表态无异议,附补充(误标当按误标处理回
  review 一次不惩罚);action_items: [] 收敛时无持有执行项,落地走待开实验'
---

# running 阶段架构级 revise plan 直接生效,不回 review——reviewer 验收对照物可能是旧版
