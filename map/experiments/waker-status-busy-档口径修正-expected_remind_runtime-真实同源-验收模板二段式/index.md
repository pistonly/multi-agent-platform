---
title: waker status busy 档口径修正：expected_remind_runtime 真实同源 + 验收模板二段式
phase: done
current_plan_version: 1
creator: host
executor: host
topic: waker-status-busy-threshold-fix
updated_at: '2026-08-31T10:07:23.372049+00:00'
description: T9：busy 档 180s 误报 vs server 30min 容忍（10× 口径差）。根因 cli/waker_status_view.py:154
  fallback 偷换 idle_stale_w。修 state.json 序列化 + 三段优先级链 + fixture-based 同帧测试 + pid zombie
  排除 + atomic write + 验收模板二段式
projection_id: d12c328c-46ad-4ce7-8440-27fc52d7c8a8
created_at: '2026-08-31T09:34:16.307198+00:00'
---

# waker status busy 档口径修正：expected_remind_runtime 真实同源 + 验收模板二段式

T9：busy 档 180s 误报 vs server 30min 容忍（10× 口径差）。根因 cli/waker_status_view.py:154 fallback 偷换 idle_stale_w。修 state.json 序列化 + 三段优先级链 + fixture-based 同帧测试 + pid zombie 排除 + atomic write + 验收模板二段式
