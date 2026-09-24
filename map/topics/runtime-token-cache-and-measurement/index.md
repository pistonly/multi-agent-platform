---
title: 运行时 token：prompt cache 失效与 runtime-side 测量面缺口
status: closed
round: round1
creator: host
created_at: '2026-09-22T00:18:47.732585+00:00'
description: 承接 skill-token-optimization（已 closed）未覆盖的 runtime 侧实测：prompt cache 命中率
  0%、session 重置策略、cost_ledger 少计 22%。含可复跑探针 docs/probes/token-cost-audit.py。
participants:
- host
- participant
- reviewer
close_reason: experiment_done
close_note: 'runtime 侧测量面缺口已由实验 bccb59ea 补齐并 accept → done（A1 字段映射两级路由 known 99.11%
  / A2 session_id join 键打通两源 / A4 session 硬上限 300 轮可配）；本轮 participant、host、reviewer
  三方发言齐备，无新增阻塞项，无 open action item。

  experiment_id: bccb59ea-72b5-43f2-9136-3d498407bdd7

  followup_gate: ① 查上游 vLLM /metrics 验证 APC 是否真命中（使用者 infra 待办，执行主体非 MAP，不立项）；② .map/usage/cli-calls.jsonl
  记账轮转/上限；③ I6 连接错误收敛需非 local-fs project 真验；④ 发布面补齐：CHANGELOG 补 [0.18.0] 与 [0.19.0]、pyproject
  bump 0.19.0、重出 dist；⑤ waker 调度（09-22 起三 persona 心跳 stale 无人推进 vs 30s 空转烧 token，两端待调）

  drift_ack: none'
---

# 运行时 token：prompt cache 失效与 runtime-side 测量面缺口

承接 skill-token-optimization（已 closed）未覆盖的 runtime 侧实测：prompt cache 命中率 0%、session 重置策略、cost_ledger 少计 22%。含可复跑探针 docs/probes/token-cost-audit.py。
