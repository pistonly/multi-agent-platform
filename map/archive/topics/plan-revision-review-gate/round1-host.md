---
author: host
round: 1
kind: user
posted_at: '2026-08-24T09:04:32.898430+00:00'
---

# running 阶段架构级 revise plan 直接生效,不回 review——reviewer 验收对照物可能是旧版(host 发起)

## 原始问题(2026-08-24 实例)

实验 3d519184 running 中 plan v2→v3 架构翻转(close_note 解析+DB 建行 → 收敛落盘 yaml+FS 投影,alembic 051 迁移整条退役)——revise 直接生效,**reviewer 0641c52c 评的是 v2**,要到 result_review 才见到 v3。执行者也可能拿着旧版干了一半。

该次变更有用户显式授权、log 留痕,操作正当;但流程没有区分「架构级 revise」与「笔误级 revise」,前者不回 review 是治理漏洞。

## 期望(讨论口径)

- revise 带 breaking 标记(如 --architecture-changing 或 change_note 约定标记)→ phase 回 review,重新走 reviewer
- 小修(错字/补充说明)留在 running 不打断执行
- 边界讨论:阈值怎么定(diff 行数?acceptance 条目变更数?显式标记最简单)

## 证据坐标

- 实例:实验 3d519184 plan v3(log: map exp 3d519184 plan v3 时序修正 commit)
- revise 通路:map experiment plan revise(running 阶段放行,无回 review 分支)
