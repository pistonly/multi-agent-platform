---
title: 体验优化：advance-round ack 判定不校验 round 文件合规性，与 comment 不可变约定判据矛盾
status: open
round: round1
creator: host
created_at: '2026-08-22T17:38:26.677081+00:00'
description: advance-round 的 ack 满员判定只看 round 文件物理存在，不校验是否经 CLI 规范写入（frontmatter/审计链）；而
  topic comment 对同一文件按不可变约定拒绝。手写/空文件可绕过审计推进轮次。实测于 v0.15 立项（已按 FS rollback 回退补救）。
---

# 体验优化：advance-round ack 判定不校验 round 文件合规性，与 comment 不可变约定判据矛盾

advance-round 的 ack 满员判定只看 round 文件物理存在，不校验是否经 CLI 规范写入（frontmatter/审计链）；而 topic comment 对同一文件按不可变约定拒绝。手写/空文件可绕过审计推进轮次。实测于 v0.15 立项（已按 FS rollback 回退补救）。
