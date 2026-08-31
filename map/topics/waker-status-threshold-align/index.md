---
title: waker status 阈值口径修正：与 map work 心跳判定对齐
status: closed
round: ready
creator: host
created_at: '2026-08-31T02:13:30.367166+00:00'
description: LIVE_WINDOW_SECONDS=30 硬编码与 active-interval 同量级致误报 stale；阈值派生自轮询配置并向
  server T2 口径对齐
participants:
- host
- participant
close_reason: experiment_done
close_note: 'T6 (a8b64c20) waker status 阈值派生实验已 accept-result done。lib/waker_status_config.py
  单模块导出 4 阈值派生函数（live_window/idle_stale/dead_window/busy_stale），cli/waker_status_view.py
  与 server/services/status_service.py 同步改造。1850 passed baseline +0 failed；waker status
  CLI + 同帧实测复核：participant/reviewer 非 busy 态 cli/server 一致；host busy 22min 态 cli=stale
  ≠ server=busy 由 v2 plan §派生公式 设计选择（CLI proxy idle_stale_w vs server settings.expected_remind_runtime_minutes）显式接受。action-items.yaml:
  无。'
---

# waker status 阈值口径修正：与 map work 心跳判定对齐

LIVE_WINDOW_SECONDS=30 硬编码与 active-interval 同量级致误报 stale；阈值派生自轮询配置并向 server T2 口径对齐
