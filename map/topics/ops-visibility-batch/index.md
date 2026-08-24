---
title: 运维可见性四件套:server status pid 失真 / 管道停滞检测未平台化 / remote 分叉无监控 / audit 无 CLI 出口
status: closed
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

# 运维可见性四件套:server status pid 失真 / 管道停滞检测未平台化 / remote 分叉无监控 / audit 无 CLI 出口
