---
title: 去除 Skills 目录对 Cursor 的绑定
status: closed
round: round2
creator: host
created_at: '2026-09-20T10:06:15.668844+00:00'
description: Skill 真身在 .cursor/skills，.claude/.codex 只是符号链接，map skill install 默认也落
  .cursor/skills；MAP 不依赖 Cursor，需解耦。
participants:
- host
- participant
- reviewer
close_reason: discussion_converged
close_note: 'experiment_id: none

  followup_gate: 6 步改造待执行；真机验收（起 server + waker 冷启动 + map skill install --runtime
  cursor 到临时目录）通过后才算闭环，实施主体待项目负责人确认'
---

# 去除 Skills 目录对 Cursor 的绑定

Skill 真身在 .cursor/skills，.claude/.codex 只是符号链接，map skill install 默认也落 .cursor/skills；MAP 不依赖 Cursor，需解耦。
