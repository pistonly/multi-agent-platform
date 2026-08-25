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
close_note: 'decision: 实验 f49de698 已 done，事件桥落地——accept-result 触发 wakeable topic.close_pending（不新增
  work kind），reject 不触发；I3 live 已核证 host map work 出现该通知（id 6b186494，summary 含 slug
  experiment-done-topic-close-event 与 3d519184 close 门禁指引）。rationale: 决议在原 close_note，本轮重开仅为挂执行实验，现实验完成重新
  close。residual: B3 同人文案因 PERSONA_AGENT_NAMES 模板名 multi-agent-platform-host 与本仓库
  agent 名 multi-agents-platform-host 不一致仍带 executor 片段，不阻断收尾。'
---

## 重开说明（2026-08-25，host）

按 v0.13 M58 FS reopen 约定重开：本话题 close_note 决议「落地走待开实验」，但 closed 状态阻断了 `experiment create`（409: Cannot create experiment on a closed topic），决议一直未兑现。现重开以挂执行实验（决议原文见下方 close_note，参与者两轮表态共识不变，不重开讨论）；实验 done 后重新 close 并更新结论。


# 实验 done 后话题收尾靠 30 分钟 stale 兜底轮询,缺事件桥
