---
title: waker 巡检视图（map waker status）：waker 自写 state + 视图只读
phase: done
current_plan_version: 1
creator: host
executor: host
topic: waker-status-and-cost-ledger
updated_at: '2026-08-31T01:22:34.958198+00:00'
description: 新增 map waker status 单命令视图：waker 自写 .map/waker-state.json (atomic write)
  + 每 cycle 末尾更新统计 + 视图只读。stale 三档 + 卡死 busy 升级 + 10 字段最小集。窄白名单 ^cli/ ^tests/。
projection_id: 715202a3-4a94-4411-996e-fd36bae42857
created_at: '2026-08-31T00:56:33.913706+00:00'
---

# waker 巡检视图（map waker status）：waker 自写 state + 视图只读

新增 map waker status 单命令视图：waker 自写 .map/waker-state.json (atomic write) + 每 cycle 末尾更新统计 + 视图只读。stale 三档 + 卡死 busy 升级 + 10 字段最小集。窄白名单 ^cli/ ^tests/。
