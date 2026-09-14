---
title: plan 文档不存 DB：分阶段 flag 门禁退役 plan_versions 全文写入
status: closed
round: ready
creator: host
created_at: '2026-09-14T10:56:21.104660+00:00'
participants:
- host
- participant
close_reason: experiment_done
close_note: 'decision: "采用分阶段 flag 门禁退役 plan_versions 全文写入：CLI 双写（--plan-file 内联镜像
  FS plan.md）→ plan_db_content_retired flag fail-closed（拒内联全文/放 slim/revise 去重换 FS
  plan.md 内容哈希）→ sync migrate 增 plan 类 kind + verify 对账 + apply stub 化 → 读路径 resolve_plan_content
  统一 fail-closed → 文档分发面更新"

  rationale: "Round 1 收敛：participant 确认四争议点并补 A（create 门禁判据=请求体是否携带全文 content_md）/B（revise
  去重与 stub 化隐性耦合）两坑，host 代码核实后吸收。实验 04cadb5c（standard，executor=participant）8 项验收全过、8
  commit 落 main（fd27873..20357a8），flag off 与现状逐字节等价、可随时回滚，reviewer 结果审批通过"

  rejected_options: "M57 式 stub 瘦身不设 flag（缺 fail-closed 门禁）；一步到位硬切换（存量内联实验断读）；拆多实验（丧失
  flag 贯穿三阶段的整体评审视角）"

  experiment_id: 04cadb5c-e546-4582-8426-401e71534e77

  followup_gate: "①dogfood 反馈 6 项由 host 转 GitHub issue：a)topic create 未声明 --participants
  则白名单无人、话题永不唤醒 b)host 侧 experiment status 显示 executor_agent_id=null 但委派实际生效 c)waker
  stdout 块缓冲致周期统计滞后 d)签名去重压制 executor 连续推进（每 item 空等最长 30 分钟）e)长寿命 resume 会话 context-length
  400 后 waker 不自动 reset 会话 f)cli/agent_client.py --effort 兜底补丁转正（MAP_RUNTIME_EFFORT
  可覆盖）②flag on 切换由 host creator/admin 按需触发并全量回归 ③存量 DB 内联 plan 在 flag on 前批量 sync
  migrate 物化"

  drift_ack: none


  补充：本次话题为纯 MAP dogfood 编排（host=交互会话，participant/reviewer 由 simple-waker 唤醒的独立 runtime
  扮演），全程零手写 map/** 文件，所有状态变更经 map CLI 完成。'
---

# plan 文档不存 DB：分阶段 flag 门禁退役 plan_versions 全文写入
