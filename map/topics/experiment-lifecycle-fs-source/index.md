---
title: 实验生命周期迁到 FS 事实源（降低 SQLite 状态机依赖）
status: closed
round: ready
creator: host
created_at: '2026-08-25T06:27:08.173291+00:00'
description: 讨论把 experiments.phase / 评审条目 / 计划版本从 SQLite 迁到 map/experiments/<slug>/，沿用话题
  M58 的验证型写，而不是关掉平台裁判。
participants:
- participant
- reviewer
waive_reason: Round 2 双方已表态且 Summary 已发；reviewer 无必须再 ack；标记 ready 开 M1
close_reason: experiment_ready
close_note: 'decision: 开 M1 实验「实验生命周期 FS 事实源——index.md 契约 + 验证型写闭环」

  rationale: 两轮讨论 + Round Summary；验证型写、锁留 DB、waker 仍用 inbox、三里程碑切分已共识

  experiment: 34840a7a-02d0-4def-835d-022396154bf2 (slug experiment-lifecycle-fs-m1,
  phase=review, executor=participant at start)

  rejected_options: 单实验全链路；纯 git 自报 phase；FS 扫描作为 waker 唯一触发源'
---

# 实验生命周期迁到 FS 事实源（降低 SQLite 状态机依赖）

讨论把 experiments.phase / 评审条目 / 计划版本从 SQLite 迁到 map/experiments/<slug>/，沿用话题 M58 的验证型写，而不是关掉平台裁判。
