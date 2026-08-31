---
title: waker runtime skill 热自检：运行中漂移检测 + 重同步 + 审计留痕
phase: done
current_plan_version: 1
creator: host
executor: host
topic: waker-skill-drift-hotcheck
updated_at: '2026-08-30T23:58:53.301542+00:00'
description: cli/simple_waker.py 主循环加周期 drift 自检（默认 30 cycles），per-skill mtime+size
  双维度快检 + hash 二次确认，发现 drift 立即 sync_runtime_skills；启动同步 + 运行中重同步均按固定 JSON schema
  留痕；新增 6 case 回归测试。窄白名单 ^cli/ ^tests/。
projection_id: d0c9dc5f-346e-48da-af35-02108befa122
created_at: '2026-08-30T22:55:51.696485+00:00'
---

# waker runtime skill 热自检：运行中漂移检测 + 重同步 + 审计留痕

cli/simple_waker.py 主循环加周期 drift 自检（默认 30 cycles），per-skill mtime+size 双维度快检 + hash 二次确认，发现 drift 立即 sync_runtime_skills；启动同步 + 运行中重同步均按固定 JSON schema 留痕；新增 6 case 回归测试。窄白名单 ^cli/ ^tests/。
