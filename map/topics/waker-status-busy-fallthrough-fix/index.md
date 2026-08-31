---
title: waker status busy 穿透误报 dead 修复（T9-B）
status: closed
round: ready
creator: host
created_at: '2026-08-31T10:30:45.715354+00:00'
description: T9 d12c328c 验收实测 smoke 失败：busy 未超阈值穿透 gap 判定，真实 busy 10min 误报 dead；修短路
  + fixture 归真 + 段二 smoke 强制
participants:
- host
- participant
close_reason: experiment_ready
close_note: 'decision: 开实验 T9-B 修 waker status busy 穿透误报 dead 根因；rationale: T9 d12c328c
  验收实测 smoke 失败（busy 624s + poll 冻结 → 误报 dead），A2 分支无 busy 短路，T9 case (a) fixture
  绕过穿透路径漏检；Round 1 host + participant 共识收口（任务三件套：穿透短路 + fixture 归真 5 case + 段二 smoke
  强制）；实验 b01d3944-79e8-4e26-a55d-e2c234facd01 计划 I1-I3 实施，验收 ≥5 case，白名单 ^cli/ ^tests/，边界不动
  lib/skill/server。'
---

# waker status busy 穿透误报 dead 修复（T9-B）

T9 d12c328c 验收实测 smoke 失败：busy 未超阈值穿透 gap 判定，真实 busy 10min 误报 dead；修短路 + fixture 归真 + 段二 smoke 强制
