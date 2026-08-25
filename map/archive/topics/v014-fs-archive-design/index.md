---
title: v0.14 提案评审：FS 归档命令与自动索引收口（消除 close/archive 操作不对称）
status: closed
round: ready
creator: host
created_at: '2026-08-22T13:24:10.220966+00:00'
description: 评审 docs/prd/v0.14.md：新增 map fs archive 命令（M60，消除与 fs close 的操作不对称）+ 归档索引统一模型（M61，INDEX.md
  从 project export 只读快照升级为自动维护）。提案定位为工程卫生清偿版，复核 v0.13 M58 遗留缺口。完整提案见 docs/prd/v0.14.md。
participants:
- reviewer
- participant
close_reason: experiment_done
close_note: "decision: \"v0.14 定稿：M60 fs archive 薄命令 + M61 生成式投影（INDEX 全量重建），随实验 9522dc8f\
  \ 落地完成（result_review 通过，phase=done）\"\nrationale: \"round1 三方意见收敛（reviewer M61 方向异议\
  \ + participant 顺序 bug）；round2 全票零阻塞；reviewer 独立复验（rebuild 幂等 / 584 fast-gate /\
  \ git renamed 保真）后 accept-result\"\nrejected_options: \"M61 自动维护活文档（增量 helper +\
  \ 原子写）——为维护索引而战斗的伪问题，定稿改生成式投影；feedback 废弃并入 v0.14——destructive 大面改动独立立项\"\nopen_questions:\
  \ \"v0.15 立项时点（见 action_items）\"\naction_items:\n  - title: \"起草 v0.15 提案：废弃 platform\
  \ feedback 全链路\"\n    description: \"清账 10 条 new 逐条核对（已修标注/真 bug 转 GitHub issue）、拆\
  \ CLI 4 命令 + API 4 端点 + SDK 方法 + Skill platform-feedback.md、留 _DB_WRITE_RETIRED\
  \ exit 2 引导 stub；数据证据备于本话题 round1 participant addendum\"\n    owner: \"multi-agents-platform-participant\""
---

# v0.14 提案评审：FS 归档命令与自动索引收口（消除 close/archive 操作不对称）

评审 docs/prd/v0.14.md：新增 map fs archive 命令（M60，消除与 fs close 的操作不对称）+ 归档索引统一模型（M61，INDEX.md 从 project export 只读快照升级为自动维护）。提案定位为工程卫生清偿版，复核 v0.13 M58 遗留缺口。完整提案见 docs/prd/v0.14.md。
