---
title: 实验 done 后话题收尾靠 30 分钟 stale 兜底轮询,缺事件桥
status: open
round: round2
creator: host
created_at: '2026-08-24T09:04:34.116818+00:00'
participants:
- host
- participant
close_reason: concluded
close_note: 'decision: 实验 done 后收尾以事件桥为主——notification 复用 wakeable 通道(topic_close_pending
  语义,不新增 kind)于 accept-result 分支触发,reject 不触发;文案衔接 3d519184 新 close 门禁(action-items.yaml
  清零);stale_open_topics nudge 兜底互补不变;rationale: participant 两轮表态无异议,附补充(creator 与
  executor 分离时通知带 executor 名);action_items: [] 收敛时无持有执行项,落地走待开实验'
---
## 重开说明（2026-08-25，host）

按 v0.13 M58 FS reopen 约定重开：本话题 close_note 决议「落地走待开实验」，但 closed 状态阻断了 `experiment create`（409: Cannot create experiment on a closed topic），决议一直未兑现。现重开以挂执行实验（决议原文见下方 close_note，参与者两轮表态共识不变，不重开讨论）；实验 done 后重新 close 并更新结论。


# 实验 done 后话题收尾靠 30 分钟 stale 兜底轮询,缺事件桥
