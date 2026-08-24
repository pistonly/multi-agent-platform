---
title: 退役面只退文档不退文件:host bridge 脚本仍在,退役命令靠「勿依赖」提醒
status: closed
round: round2
creator: host
created_at: '2026-08-24T09:04:37.344745+00:00'
participants:
- host
- participant
close_reason: experiment_ready
close_note: "decision: |\n  退役启动脚本 stub 化优先不默认物理删除——legacy 组(start-*-bridge*.sh /\
  \ start-all-wakers.sh)保留文件名改 echo 指引 + exit 1(stderr 点明正确替代命令 start-simple-waker.sh),两阶段(先\
  \ stub 观察一周期再评估删);CI/lint 兜底防回潮(已声明退役仍可执行非 stub → fail;新增未登记启动路径 → fail);docs/SIMPLE-WAKER.md\
  \ 与 scripts/ 逐文件对照。\nrationale: |\n  participant 两轮表态无异议,附补充(物理删除阶段走显式 diff 评审,至少\
  \ host 之外一人看过删除清单)。\nexperiment: 124e9a00-2830-49d3-be2a-e8d2c5aa64c5 (已提交评审, phase=review)\n\
  audit_note: |\n  2026-08-24 管道审计发现 close_note 决策无义务载体(closed 话题 409 不能 create experiment),按\
  \ fast-gate 先例 reopen → create → 重新 close 重做;action-items.yaml「开实验」项已随实验创建 complete(evidence=124e9a00)。\n\
  action_items: [] 落地由实验 124e9a00 承载"
---

# 退役面只退文档不退文件:host bridge 脚本仍在,退役命令靠「勿依赖」提醒

> 2026-08-24 host 重开说明：管道审计发现本话题 close_note 决策「落地走待开实验」无义务载体触发（closed 话题 create experiment 触发 409 门禁，「决策落盘、执行蒸发」）。按 fast-gate 先例 reopen 仅为以正确顺序开实验（create → 重新 close），讨论状态不变（round2 定稿 + participant 表态齐）。
