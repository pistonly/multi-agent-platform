---
title: v0.15 提案评审：废弃 platform feedback 全链路（死信箱收口）
status: closed
round: ready
creator: host
created_at: '2026-08-22T17:33:43.908667+00:00'
description: 评审废弃 platform feedback 全链路的提案：清账 11 条存量、拆 CLI 4 命令 + API 4 端点 + SDK 方法
  + Skill 文档、留 _DB_WRITE_RETIRED exit 2 引导 stub、DB 表保留只读。证据源于 v014 话题 round1 participant
  addendum（死信箱学习效应 + 自托管收件人错位分析）。争议点：废弃 vs 修复（webhook 转发）。
participants:
- reviewer
- participant
close_reason: experiment_done
close_note: 'decision: "v0.15 定稿：废弃 platform feedback 全链路（死信箱收口），M62 随实验 d1cae41e
  落地完成（result_review 含一轮驳回返工后通过，phase=done）"

  rationale: "round2 全票闭合四争议（reviewer 四条独立证据裁废弃定案 + participant 10/10 核证补强：已修却 0/10
  关单，闭环记账从未发生）；清账 11/11 resolved 先于拆除（时序门），拆除九路含 Web 前端链路与 Skill 三层（源/分发源/runtime
  home），防半拆 grep 达标"

  rejected_options: "webhook 转发等修复方案——修复面等于在 MAP 核心重建 issue tracker，违反薄核心定位；保留 admin
  triage 入口——update 与只读承诺直接矛盾"

  artifacts: "清账处置表 map/experiments/m62-feedback-deprecation/log-r1.md（DB 只读化后唯一人类友好索引）；返工记录
  log-r3.md；PRD docs/prd/v0.15.md（含 SDK breaking 声明）"

  open_questions: "无；关联独立话题 fs-advance-ack-validation（advance-round ack 合规性校验缺口）另行推进"'
---

# v0.15 提案评审：废弃 platform feedback 全链路（死信箱收口）

评审废弃 platform feedback 全链路的提案：清账 11 条存量、拆 CLI 4 命令 + API 4 端点 + SDK 方法 + Skill 文档、留 _DB_WRITE_RETIRED exit 2 引导 stub、DB 表保留只读。证据源于 v014 话题 round1 participant addendum（死信箱学习效应 + 自托管收件人错位分析）。争议点：废弃 vs 修复（webhook 转发）。
