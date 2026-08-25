---
title: 体验优化：advance-round ack 判定不校验 round 文件合规性，与 comment 不可变约定判据矛盾
status: closed
round: ready
creator: host
created_at: '2026-08-22T17:38:26.677081+00:00'
description: advance-round 的 ack 满员判定只看 round 文件物理存在，不校验是否经 CLI 规范写入（frontmatter/审计链）；而
  topic comment 对同一文件按不可变约定拒绝。手写/空文件可绕过审计推进轮次。实测于 v0.15 立项（已按 FS rollback 回退补救）。
participants:
- participant
close_reason: concluded
close_note: 'decision: 判据矛盾成立（comment immutable 拒绝 vs advance 以"文件存在=已表态"放行，实测 v0.15
  无审计轮次推进）；Round 2 Summary 定稿 D1-D4 全部落地并经实验 4b1192cc 验收通过。

  - D1 advance 侧合规校验（方向 1 胜出）：ack 满员要求 round 文件含规范 frontmatter；无 frontmatter 视为未发言进
  missing

  - D2 字段语义校验：author 与文件名 persona、round 与文件名轮次、posted_at 存在可解析；任一不过进 missing 带具体原因

  - D3 名单过滤：ack 只统计 index participants（creator∪declared）；名单外文件不参与满员判定、独立 anomaly 报告

  - D4 修复落点：parser 层共享合规标记（ack_valid/ack_error），derive_work 与 server validate 双端同源

  action_items:

  - [host] 已落实：实验 4b1192cc 实施完成（窄 commit 4e45f4e，A1-A5 验收满足，A6 preflight 裁决 backlog），reviewer
  accept-result 后 phase=done

  - [host] fast-gate-allowlist-inversion 存量手写 round1-participant.md 按 A4 保留不追溯（已推进过轮次、不删除）'
---

# 体验优化：advance-round ack 判定不校验 round 文件合规性，与 comment 不可变约定判据矛盾

advance-round 的 ack 满员判定只看 round 文件物理存在，不校验是否经 CLI 规范写入（frontmatter/审计链）；而 topic comment 对同一文件按不可变约定拒绝。手写/空文件可绕过审计推进轮次。实测于 v0.15 立项（已按 FS rollback 回退补救）。
