---
title: waker 心跳 busy/dead 拆分：长会话期间心跳与忙状态分离
status: closed
round: ready
creator: host
created_at: '2026-08-30T19:24:57.648274+00:00'
description: waker 进长 runtime 会话不更新心跳致 stale 误报；新增 session_busy_since，map work 区分
  busy/stale
participants:
- host
- participant
close_reason: experiment_done
close_note: '实验 b3ec2e4d 已 reviewer accept-result 通过（通知 event: experiment.phase_changed
  → done）。


  Round 1 共识（host + participant 3 轮表态 + summary-host 收口）：

  - T1 busy 状态机：_touch_busy 写 session_busy_since + busy_pid + busy_started_at；finally
  _clear_busy（同 PID）+ 启动期 _check_busy_crash_recovery（跨 PID，kill -0 self-check）

  - T2 busy vs stale：status_service 区分 busy_since 非空 → busy_tolerance（max(expected_remind_runtime,
  2 × threshold)），超阈值才 stale；map work 渲染 busy > 2h 软警告

  - T3 窄白名单：cli/ + server/ + sdk/ + tests/，不引入新 wake signature / work_items kind


  action-items.yaml 不存在，门禁通过。直接 close。'
---

# waker 心跳 busy/dead 拆分：长会话期间心跳与忙状态分离

waker 进长 runtime 会话不更新心跳致 stale 误报；新增 session_busy_since，map work 区分 busy/stale
