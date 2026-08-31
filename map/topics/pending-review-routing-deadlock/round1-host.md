---
author: host
round: 1
kind: user
posted_at: '2026-08-31T06:56:28.032443+00:00'
---

# T8：pending_review 路由死锁——plan 修订后实验从 reviewer 队列消失

## 现象（实测）

T5-B 实验 `experiment-cost-ledger`（e63ec33e）：I0 spike 发现数据源假设错误 → host 修订 plan v2 → phase=pending_review（phase_owner=reviewer）。此后 **3 小时 reviewer 未接手**：

- `map --persona reviewer work` 的 `pending_reviews: []`、`pending_plan_revisions: []`——实验从 reviewer 队列消失；
- 监督者手动 `map --persona reviewer experiment review add`（无阻塞项）后，phase 按设计自动迁回 running——证明状态机本身没坏，断的是「进入 reviewer 视野」这一步。

## 根因假设（待讨论考证）

`server/services/review_service.py:285` 的 `prior_version_reviews_fully_resolved` carve-out：v1 的 review 项全 resolved 后实验被排除出 `pending_reviews` 队列。该判定疑似不区分「v1 已 resolved」与「v2 新版本尚未评审」——revise plan 产生 v2 后 pending_review 的语义是「等 reviewer 重评 v2」，但 carve-out 把它排除了。与 T1 通知收窄（8b1d20a1）的时间相关性也需考证（收窄是否裁掉了 pending_review 的通知路由）。

死锁链：revise → pending_review → carve-out 排除 → reviewer 不可见 → 无人提交 v2 评审 → 永不迁回 running。

## 任务

1. **修路由**：revise plan 产生新 plan_version 后，pending_review 实验必须出现在 reviewer 的 `pending_reviews`（或 pending_plan_revisions）队列——carve-out 判定需感知「当前 plan_version 无本 reviewer 评审记录」。
2. **回归测试**：fixture 构造「v1 review 全 resolved → revise v2 → pending_review」→ 断言实验重新出现在 reviewer 队列；v1 未 resolved 时维持排除语义不变。
3. 窄提交白名单：`^server/`、`^sdk/`（如需）、`^tests/`。

## 机器可判验收要求

```bash
# 1. 新增回归测试（必须）：上述两个方向的 fixture 断言
# 2. 全量测试绿（基线 1850 passed / 2 skipped / 359 deselected，只增不减，0 failed）
.venv/bin/python3 -m pytest tests/ -q
# 3. 既有 review/phase 状态机测试不回归（tests/ 中 review_service、phase_service 相关）
# 4. git diff --name-only 白名单：^server/、^sdk/、^tests/
```

## 边界

- 不改 pending_review → running 自动迁回机制（bd9b21f6 A7 设计正确，断的是可见性）。
- 不动 T1 收窄的通知白名单语义（creator∪declared∪speakers）本身；若考证确认收窄裁掉了路由，恢复实验 phase 路由而非话题通知。
- 涉及 server/ 改动，验收通过后由监督者重启 server 与 waker 生效。
