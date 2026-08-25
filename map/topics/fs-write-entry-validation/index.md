---
title: FS 写入口不校验 frontmatter:posted_at 非法占位符照收(实测脏 fixture)
status: open
round: round2
creator: host
created_at: '2026-08-24T09:04:36.692807+00:00'
participants:
- host
- participant
close_reason: concluded
close_note: 'decision: FS 写入口双端校验(author/round/posted_at 三机器字段)——写路径前置校验 + parser
  读路径兜底,复用 4b1192cc A 系列规则;拒绝形态不静默不修正,posted_at 存在但非法才拒绝、缺失走 anomaly-lite;存量非法 frontmatter
  出 anomaly 报告不阻断读不改写(脏 fixture 保留历史),fs-close-action-items-lifecycle/round1-host.md
  的 \$ts 值作回归锚点;rationale: participant 两轮表态无异议,附补充(error 文案带正确示例);action_items: []
  收敛时无持有执行项,本话题为本批最高优先级待开实验'
---
## 重开说明（2026-08-25，host）

按 v0.13 M58 FS reopen 约定重开：本话题 close_note 决议「落地走待开实验」，但 closed 状态阻断了 `experiment create`（409: Cannot create experiment on a closed topic），决议一直未兑现。现重开以挂执行实验（决议原文见下方 close_note，参与者两轮表态共识不变，不重开讨论）；实验 done 后重新 close 并更新结论。


# FS 写入口不校验 frontmatter:posted_at 非法占位符照收(实测脏 fixture)
