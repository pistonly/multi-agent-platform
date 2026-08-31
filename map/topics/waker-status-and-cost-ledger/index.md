---
title: waker 可观测性统一视图 + per-实验 token 成本台账
status: closed
round: ready
creator: host
created_at: '2026-08-31T00:21:18.653079+00:00'
description: 巡检靠拼 pgrep/map work/tail/pstree 四件套；实验 token 成本无台账；讨论后可拆两实验
participants:
- host
- participant
waive_reason: 议题已收敛：participant round1 已完整表态（方向 A+B 4+4 护栏 + 强烈拆分建议 + 5 我补 case +
  3 隐含边界），host round2 全部采纳并锁死 5 条未决项；额外 round2 ack 无新增信息，属礼貌性 ack 而非内容性 ack；按 host-checklist
  §2 走 waive-ack 路径推到 ready 直接开 expA + expB。
close_reason: T5-A 实验 715202a3 (waker 巡检视图) 已 done：I1 设计收敛 + I2 cycle 累加 + I3 map
  waker status CLI + I4 15 case 全过 + I5 窄 commit 57d76e5 + 全量 pytest 1818/0 failed。验收通过后由监督者重启
  simple-waker 让 I2 字段开始累加。T5-B (per-实验 token 成本台账) 按 round2 共识待开新实验。
---

# waker 可观测性统一视图 + per-实验 token 成本台账

巡检靠拼 pgrep/map work/tail/pstree 四件套；实验 token 成本无台账；讨论后可拆两实验
