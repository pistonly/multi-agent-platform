---
title: 测试债:主干 26 个预存红放大一切验收成本 + 实验 complete 的测试证据无人校验
status: closed
round: round2
creator: host
created_at: '2026-08-24T09:04:33.466494+00:00'
participants:
- host
- participant
close_reason: experiment_ready
close_note: "decision: |\n  主干 26 红 P0 先清零——优先级 CLI format/envelope 族(9 红同一根因 JSONDecodeError\
  \ Extra data)→ map_sdk_skeleton 误报(2 红异因)→ 零散三组 → 两只碎红;complete 的 pytest_summary\
  \ 机器校验 failed>0 拒绝 + total 与 CI 不符 warning,复用 evidence_metadata 不另起炉灶,--known-failures\
  \ <ref> 显式豁免;accept-result 侧 reviewer 可视化;fast-gate 顺带存量真 slow/integration 打标;防新漂移(修过严断言非放宽被测逻辑);顺带并入管道审计小洞:action-item\
  \ add 对 closed 话题校验。\nrationale: |\n  participant 两轮表态无异议,附补充(probe test_zz_fastgate_probe.py\
  \ 与 26 红分开标注,验收口径=26 红清零+fast-gate 正常集,probe 仅附注)。\nexperiment: 50cddb7e-b16d-4ad0-bf0a-684bd72984ac\
  \ (已提交评审, phase=review)\naudit_note: |\n  2026-08-24 管道审计发现 close_note 决策无义务载体(closed\
  \ 话题 409 不能 create experiment),按 fast-gate 先例 reopen → create → 重新 close 重做;action-items.yaml「开实验」项已随实验创建\
  \ complete(evidence=50cddb7e)。\naction_items: [] 落地由实验 50cddb7e 承载"
---

# 测试债:主干 26 个预存红放大一切验收成本 + 实验 complete 的测试证据无人校验

> 2026-08-24 host 重开说明：管道审计发现本话题 close_note 决策「落地随实验落地」无义务载体触发（closed 话题 create experiment 触发 409 门禁，「决策落盘、执行蒸发」）。按 fast-gate 先例 reopen 仅为以正确顺序开实验（create → 重新 close），讨论状态不变（round2 定稿 + participant 表态齐）。
