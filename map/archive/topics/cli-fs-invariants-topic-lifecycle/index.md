---
title: CLI/FS 不变量包：topic create 防 slug 冲突 + close 查实验 terminal
status: closed
round: ready
creator: host
created_at: '2026-08-30T20:53:01.303831+00:00'
description: create 遇既有 slug 静默覆盖 front-matter；close 不查关联实验 terminal；补不变量+门禁+回归测试
participants:
- host
- participant
close_reason: experiment_done
close_note: '实验 7aeabc2e-8798-4177-b178-45799caa4382 accept（commit b3e1924，1735 passed
  + 2 skipped + 0 failed）。I1-I6 全闭环：write_topic_index overwrite 默认拒绝 + created_at
  不可变 + FsTopic.experiments scan_plane 注入 + validate_close 第 4 维校验 + CLI topic create
  --force + close OpenExperimentError 渲染 + 11 case 双层回归测试。known-failures:无；follow-up:独立
  map topic amend --created-at 命令（plan §风险 与边界 8）。close_reason: experiment_done（实验
  b3ec2e4d 链路）。'
---

# CLI/FS 不变量包：topic create 防 slug 冲突 + close 查实验 terminal

create 遇既有 slug 静默覆盖 front-matter；close 不查关联实验 terminal；补不变量+门禁+回归测试
