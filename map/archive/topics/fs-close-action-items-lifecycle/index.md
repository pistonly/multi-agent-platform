---
title: 话题闭环漏洞:FS close_note 的 action_items 无解析无跟踪,执行项蒸发(merge 未执行即关)
status: closed
round: ready
creator: host
created_at: '2026-08-24T00:38:12.146910+00:00'
participants:
- host
- participant
waive_reason: participant round1 已完整表态(方案2主+1辅+格式约定化+存量merge补执行)且零异议,host Round2 Summary
  采录入案;议题已收敛,不追加 round2 空表态
close_reason: experiment_ready
close_note: "decision: \"转向 FS close_note 的 action_items 解析入跟踪(方案 2 为主):close 时解析结构化\
  \ action_items 段复用 DB TopicActionItem 建行与全套升级/complete 机制,举方案 1 门禁(status: open\
  \ 行 → 409 引导先 done / 显式转跟踪)为辅助防线;close_note action_items 格式约定化为 owner/title/status\
  \ 结构化段\"\nrationale: \"merge-github-main-into-local 的执行项蒸发证实断链:FS close 只写纯文本 close_note,无解析无建行,close\
  \ 后义务真空。DB 侧机制(topic_resolve_service.py:175 建行 / todo_service 桶 / waker WAKE/STALE\
  \ 升级 / CLI complete 出口)完整可就地复用,不另建 FS 侧任务跟踪。participant round1 完整表态支持该口径并细化出格式约定。议题收敛后直接开实验(四门\
  \ Rubric 通过),不追加 round2 空表态\"\nrejected_options: \"方案 1 独奏(门禁):只堵 close 时点瞬间,无跟踪且易诱发\
  \ close_note 不写执行项的规避;方案 3 混合以边界简化后并入方案 2+1 组合;reopen merge 话题(重型操作,补执行 + 本话题记录成本更低)\"\
  \nopen_questions:\n  - \"门禁细节二选一(status: open 行 409 直接拦 vs 转跟踪自动放行)带入实验 3d519184\
  \ plan 定案\"\n  - \"存量已 close 话题回填范围与幂等 key(title 是否够)带入实验 plan 定案\"\n  - \"posted_at='$ts'\
  \ 脏 fixture(本话题 round1-host.md)作观察项,是否修另判\"\naction_items:\n  - title: \"实施实验 3d519184:FS\
  \ close_note action_items 解析入跟踪\"\n    owner: \"multi-agents-platform-host\"\n \
  \   linked_experiment: \"3d519184-a59f-4e0a-9068-19c7d02be142\"\n  - title: \"存量\
  \ merge-github-main-into-local 回填可查并补执行(push github 前用户显式确认)\"\n    owner: \"multi-agents-platform-host\"\
  \n    linked_experiment: \"3d519184-a59f-4e0a-9068-19c7d02be142\""
---

# 话题闭环漏洞:FS close_note 的 action_items 无解析无跟踪,执行项蒸发(merge 未执行即关)
