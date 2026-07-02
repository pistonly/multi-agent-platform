# Phase 1 实验：waker 增量 diff 轮询 + 三件基础设施（polling-only）

## 来源
- 话题：`cfd1578e-dd6f-473a-85c2-a02797718d41`（是否可以将轮询的 waker 改为 hook 的方式）
- 两轮讨论收敛：混合方案（SSE/事件驱动 + 极低频兜底轮询），**不做纯 hook**，分两阶段。本实验 = **Phase 1**，polling-only。

## 目标 / 非目标
- **目标**：保持轮询主路径，把三件基础设施（`inbound_event` 表 + fingerprint 先写盘后 resume + 服务端重放拒绝）在简单 polling 路径上做对，为 Phase 2（SSE 叠加）铺平幂等与审计语义。
- **非目标（留 Phase 2）**：SSE 长连切换、Cursor SDK hook、兜底频率上调、6 个 lifecycle publish 调用点。

## 交付物（显式，便于 ack 锚定 + reviewer 5 项红线映射）

### D1. `inbound_event` 表 + migration
- 新建表：`inbound_event(id, persona, event_id, event_type, received_at, source, payload, acked_at, fingerprint)`
- `event_id` 与 `notification.id` **一一对应**（非自增），便于与 `notification` 表 join
- 索引：`UNIQUE(fingerprint)` + `COMPOSITE(persona, source, received_at)`
- `source` 枚举：`polling` / `sse` / `replay`（Phase 1 实际只写 `polling`，但 enum 一次到位）
- 审计三段分层：`notification`（事件层）/ `inbound_event`（接入层）/ `runtime-waker-sessions/*.jsonl`（执行层）

### D2. `todos` last-id 游标增量读取
- `cli/runtime_waker.py` 当前每次 wake 全量读 `map todos`；改为维护 `since=<last_seen>` 游标只拉增量
- 在 polling 路径上先验证幂等去重语义，网络/解析成本下降

### D3. fingerprint 写盘 **先于** resume 的顺序保证
- `.map/runtime-waker-state-<persona>.json` 持久化 fingerprint 必须在 resume Agent Runtime **之前**完成
- 避免 resume 崩溃导致重投双 wake（reviewer Round 1 强调，是重复唤醒率 + 幂等的共同根因）

### D4. 服务端层重放拒绝（幂等写成功率 100%）
- 服务端对同一 `event_id` / `fingerprint` 重放 100% 拒绝
- 客户端 fingerprint 兜底（D3）作第二道闸

### D5. sessions jsonl 补 `event_source` 字段
- `runtime-waker-sessions/<id>.jsonl` 每条记录补 `event_source`（`polling`/`sse`/`replay`）
- 便于事后区分 wake 来源路径

## 硬性约束（不可违背）
- hook 路径**只跑 Claude Code CLI**；Cursor SDK / Codex 继续走轮询兜底（钩子能力不对齐，混用引入新耦合）
- 兜底轮询**永不取消**（SSE 多稳都保留作最后一道防线）
- 兜底频率走 `MAP_RUNTIME_INTERVAL`，**Phase 1 不锁死**（Phase 2 实测 SSE 漏事件率 < 0.1% 后再议 15min）

## 验收（断言式测试，全部必须通过）
- **A1 重放拒绝**：服务端层重放 100 次同 `event_id`，0 成功（→ D4）
- **A2 并发注入零误唤醒**：同 fingerprint 并发注入，只 wake 一次（→ D3 + D4）
- **A3 审计三段可 join**：`notification.id` ↔ `inbound_event.event_id` ↔ `sessions jsonl` 三表可 join 对齐，无丢失/错位（→ D1 + D5）
- **A4 P95 仅记基线**：记录 polling-only P95 基线数字，**不设准入门槛**（P95 改善是 Phase 2 的活）
- **A5 `source` 正确**：Phase 1 所有 `inbound_event.source` = `polling`（→ D1 + D5）

## 回归基线（进 Phase 2 的前置）
- A1–A3、A5 全部断言通过；A4 基线已记录作 Phase 2 对照

## 风险
- `cli/runtime_waker.py` 当前 branch（agent-runtime）已有未提交改动——实施前先 `git commit` checkpoint
- fingerprint 写盘顺序改动影响现有去重逻辑，须保证不回退当前去重能力

## 实施步骤（running 阶段逐子项推进）
- **I1**：`inbound_event` migration + ORM model（→ D1）
- **I2**：服务端重放拒绝（→ D4）
- **I3**：waker fingerprint 先写盘后 resume 顺序（→ D3）+ todos 增量读取（→ D2）
- **I4**：sessions jsonl `event_source`（→ D5）
- **I5**：验收测试 A1–A5 + 记录 P95 基线（→ A4）
