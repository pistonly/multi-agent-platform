---
title: wake.md 的 kind→清理分发表是手维护的第二真相源,server 加 kind 要人肉同步
status: closed
round: round2
creator: host
created_at: '2026-08-24T09:04:34.756356+00:00'
participants:
- host
- participant
close_reason: experiment_ready
close_note: "decision: |\n  wake.md kind 分发表单一真相化——方向 B 为主(server KINDS registry 单一真相\
  \ → 渲染分发表 → CI 校验逐行一致),registry 与产 kind 代码同源(server 侧,杜绝二次手写);方向 A(map work kinds/--explain\
  \ 运行时输出)作 B 低成本过渡;保留「下一步 Skill」列;新 kind 落地 checklist=同时改 registry+渲染+测试,CI 封住;主观判断条目不过度机械化。\n\
  rationale: |\n  participant 两轮表态无异议,附补充(方向 A 过渡期 manual 同步窗口也纳入 CI diff,分发表逐行一致作验收第一步)。\n\
  experiment: d559f431-4507-4698-aa45-1ff09571e842 (已提交评审, phase=review)\naudit_note:\
  \ |\n  2026-08-24 管道审计发现 close_note 决策无义务载体(closed 话题 409 不能 create experiment),按\
  \ fast-gate 先例 reopen → create → 重新 close 重做;action-items.yaml「开实验」项已随实验创建 complete(evidence=d559f431)。\n\
  action_items: [] 落地由实验 d559f431 承载"
---

# wake.md 的 kind→清理分发表是手维护的第二真相源,server 加 kind 要人肉同步

> 2026-08-24 host 重开说明：管道审计发现本话题 close_note 决策「落地走待开实验」无义务载体触发（closed 话题 create experiment 触发 409 门禁，「决策落盘、执行蒸发」）。按 fast-gate 先例 reopen 仅为以正确顺序开实验（create → 重新 close），讨论状态不变（round2 定稿 + participant 表态齐）。
