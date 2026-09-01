---
title: skill 分发热自检：waker 运行中源漂移检测与重同步
status: closed
round: ready
creator: host
created_at: '2026-08-30T22:51:03.536479+00:00'
description: sync_runtime_skills 仅启动时跑；运行中 .cursor/skills 更新不热同步且无留痕；加周期自检+自动重同步+审计日志
participants:
- host
- participant
waive_reason: host round1 已交(round1-host.md 22:51:04),waker 误报 host ack 待办;participant
  缺文件但本轮为 host 主导的开场轮,先推进到 round2 让 participant 接 round2 视角
close_reason: experiment_done
close_note: 实验 d0c9dc5f-346e-48da-af35-02108befa122 done（reviewer accept-result），DriftDetector
  模块 + simple_waker 周期自检 + 启动/运行双留痕落地。9 case 回归测试全过；pytest 1791 passed（基线 1782 + 新增
  9）；ruff check 0；commit 5892a83 窄白名单 ^cli/ ^tests/。sync_runtime_skills 语义不变，仅签名扩
  tuple 返回供 audit 消费。无遗留 action_items。
---

# skill 分发热自检：waker 运行中源漂移检测与重同步

sync_runtime_skills 仅启动时跑；运行中 .cursor/skills 更新不热同步且无留痕；加周期自检+自动重同步+审计日志
