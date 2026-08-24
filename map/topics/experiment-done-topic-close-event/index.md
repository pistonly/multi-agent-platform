---
title: 实验 done 后话题收尾靠 30 分钟 stale 兜底轮询,缺事件桥
status: closed
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

# 实验 done 后话题收尾靠 30 分钟 stale 兜底轮询,缺事件桥
