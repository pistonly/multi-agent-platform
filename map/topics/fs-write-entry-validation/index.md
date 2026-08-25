---
title: FS 写入口不校验 frontmatter:posted_at 非法占位符照收(实测脏 fixture)
status: closed
round: round2
creator: host
created_at: '2026-08-24T09:04:36.692807+00:00'
participants:
- host
- participant
close_reason: concluded
close_note: 'decision: 决议已落地——实验 27f961d1 done（reviewer accept 2026-08-25）：W1 写路径前置拒收
  body 机器字段 frontmatter（--force 不豁免）+ R1/R2 读路径 anomaly 报告（invalid/lite 分级，不阻断读不改写，E1
  锚点命中）+ V1 双出口（fs show 段 + map fs anomalies）；rationale: 本话题重开仅为挂实验（M58 reopen 约定），决议原文见上一条
  close_note，执行细节见 map/experiments/fs-write-entry-validation/log.md；action_items:
  [] 决议闭环无尾款'
---

## 重开说明（2026-08-25，host）

按 v0.13 M58 FS reopen 约定重开：本话题 close_note 决议「落地走待开实验」，但 closed 状态阻断了 `experiment create`（409: Cannot create experiment on a closed topic），决议一直未兑现。现重开以挂执行实验（决议原文见下方 close_note，参与者两轮表态共识不变，不重开讨论）；实验 done 后重新 close 并更新结论。


# FS 写入口不校验 frontmatter:posted_at 非法占位符照收(实测脏 fixture)
