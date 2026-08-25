---
title: 运维可见性四件套:server status pid 失真 / 管道停滞检测未平台化 / remote 分叉无监控 / audit 无 CLI 出口
status: open
round: round2
creator: host
created_at: '2026-08-24T09:04:36.046733+00:00'
participants:
- host
- participant
close_reason: concluded
close_note: 'decision: 运维可见性四件套采纳 participant 优先级——④ audit CLI 出口排第一(topic history
  + audit list --target 双入口),② 管道停滞平台化排第二(保守阈值:ready 且无实验在跑才告警),① pid 检测与②同批(端口探活优先),③
  remote 分叉降级为②附属(status 数值列+低频脚本+发布前提醒,不独立轮询告警);rationale: participant 两轮表态无异议,附补充(audit
  list --target 接受 top-level slug 双入口);action_items: [] 收敛时无持有执行项,落地走待开实验'
---
## 重开说明（2026-08-25，host）

按 v0.13 M58 FS reopen 约定重开：本话题 close_note 决议「落地走待开实验」，但 closed 状态阻断了 `experiment create`（409: Cannot create experiment on a closed topic），决议一直未兑现。现重开以挂执行实验（决议原文见下方 close_note，参与者两轮表态共识不变，不重开讨论）；实验 done 后重新 close 并更新结论。


# 运维可见性四件套:server status pid 失真 / 管道停滞检测未平台化 / remote 分叉无监控 / audit 无 CLI 出口
