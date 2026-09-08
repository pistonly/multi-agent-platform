---
title: 体验优化：simple-waker 唤醒目标支持既有交互会话
status: closed
round: ready
creator: host
created_at: '2026-09-03T16:56:27.398060+00:00'
participants:
- participant
close_reason: experiment_done
close_note: 'decision: ①独立 Stop hook 会话桥接组件（与 waker 解耦、协议统一）②桥接本地 state 实例分离③软信号互斥（last_seen_at
  心跳→waker 降级，宁重复不遗漏）④本期只做 Claude Code，cursor 出范围⑤重复提醒有限升级≤3后沉默、非阻塞⑥Skill 新增工作方式启动协商小节（A/B/C/D）。实验
  db97aeac-df9b-47de-b042-c0259f3a5881 已 done（standard，窄提交 5f57412，2120p/1f 存量豁免）;
  rationale: Round 1 host+participant+reviewer 三方收敛，计划/结果两轮 reviewer 门禁通过; rejected_options:
  waker 内置 interactive-bridge runtime 通道（解耦优先）、强锁互斥、共享 state 文件; 遗留: A6 段二真实联动 smoke（装
  hook 收提醒 + waker 降级）由监督者手动确认'
---

# 体验优化：simple-waker 唤醒目标支持既有交互会话
