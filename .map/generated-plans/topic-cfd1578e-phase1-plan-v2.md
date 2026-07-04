# Phase 1 实验：waker 增量 diff 轮询 + 三件基础设施（polling-only）· v2

> **v2 变更**：回应 reviewer 评审（`a0671c0e`）。两个 unreasonable 项已修订：
> - **U1 (`1f57d5f9`)**：A3 三表 join 在 sessions jsonl 侧缺 join key → **D5 扩展**为补 `event_source` + `event_id`（+ 可选 `fingerprint`），使 A3 join 可落地。
> - **U2 (`23643121`)**：D4「服务端层重放拒绝」机制与写入路径未定义 → **新增 D6**，明确 waker 在 resume 前调用服务端「record fingerprint」写入端点（`inbound_event` 的 `UNIQUE(fingerprint)` 即拒绝机制），作为 A1/A2 **主闸**；D3 客户端写盘降为**第二道闸**。
> 同时吸收 5 条 reasonable 澄清（D1 agent_id 外键 / D2 游标语义 / D3 重构方法论 / A2 并发测试设施 / A4 测量定义）。

## 来源
- 话题：`cfd1578e-dd6f-473a-85c2-a02797718d41`（是否可以将轮询的 waker 改为 hook 的方式）
- 两轮讨论收敛：混合方案（SSE/事件驱动 + 极低频兜底轮询），**不做纯 hook**，分两阶段。本实验 = **Phase 1**，polling-only。

## 目标 / 非目标
- **目标**：保持轮询主路径，把三件基础设施（`inbound_event` 表 + 服务端 record-fingerprint 写入主闸 + 客户端 fingerprint 写盘二闸）在简单 polling 路径上做对，为 Phase 2（SSE 叠加）铺平幂等与审计语义。
- **非目标（留 Phase 2）**：SSE 长连切换、Cursor SDK hook、兜底频率上调、6 个 lifecycle publish 调用点、服务端 `todos since=` 增量 API（见 D2）。

## 交付物（显式，便于 ack 锚定 + reviewer 5 项红线映射）

### D1. `inbound_event` 表 + migration
- 新建表：`inbound_event(id, agent_id, event_id, event_type, received_at, source, payload, acked_at, fingerprint)`
- **`agent_id` 为外键** `ForeignKey("agents.id")`，对齐 `Notification.recipient_agent_id`（`server/domain/models.py:377`），便于 `notification ↔ inbound_event` 按 UUID join，避免字符串(persona name) vs UUID 口径分裂（吸收 reasonable `c9ee44ce`）。persona 名作为 `payload` 内的冗余字段保留，不作为 join key。
- `event_id` 与 `notification.id` **一一对应**（UUID，非自增），便于与 `notification` 表 join
- 索引：`UNIQUE(fingerprint)` + `COMPOSITE(agent_id, source, received_at)`
- `source` 枚举：`polling` / `sse` / `replay`（Phase 1 实际只写 `polling`，但 enum 一次到位）
- 审计三段分层：`notification`（事件层）/ `inbound_event`（接入层）/ `runtime-waker-sessions/*.jsonl`（执行层）

### D2. `todos` 增量读取（**澄清游标语义**，吸收 reasonable `510e9e3b`）
- **游标字段**：`notification.created_at`（已索引，`server/domain/models.py:385`，时间单调）；不用 `notification.id`（UUID 非单调）。
- **服务端现状**：`map todos` 是聚合视图（pending_reviews / pending_replies / mentions / action_items 多维度），客户端 `todos()` 无 `since` 参数（`sdk/python/.../map_command_client.py:52-53`）。
- **Phase 1 范围**：先做**客户端游标**——waker 在 state 文件记 `last_seen_notification_created_at`，对已拉的 todos 事件按 `created_at > cursor` 客户端过滤，验证幂等去重语义。**服务端 `since` 增量 API 属网络成本优化，显式留 Phase 2**（polling-only 阶段网络成本非准入项）。
- 明确「只拉增量」语义：每 tick 仍全量拉 todos（服务端无 since 支持），增量过滤发生在客户端；本交付物的价值是**去重语义**而非降网络成本。

### D3. fingerprint 写盘先于 resume —— **回归测试固化，非盲目重构**（吸收 reasonable `e18bb4a9`）
- **现状核实**：`cli/runtime_waker.py` 当前 resume 在 `_wake_event` 内（`:565`/`:567`），`_mark_event(status="woken")` 在 resume 之后（`:578`），state 落盘在循环末尾 `_save_state_if_needed()`（`:524`）或 `_prepare_session_for_event` 的 force save（`:557`）。即当前顺序是 **resume → mark woken → 循环末落盘**，resume 与落盘之间存在崩溃窗口。
- **D3 动作**：把 fingerprint 落盘**收紧到 resume 之前**（resume 前先 `_mark_event` + `_save_state_if_needed(force=True)`），并**新增回归测试断言该顺序**——若 resume 抛异常或进程崩溃，下次启动不会重复 wake 同一 fingerprint。
- **方法论**：先加「固化当前/目标顺序」的回归测试再改顺序，保证不回退现有去重能力（呼应风险段）。本项是**客户端第二道闸**（崩溃安全），主闸见 D6。

### D4. 服务端层重放拒绝（幂等写成功率 100%）—— 机制由 D6 承载
- 服务端对同一 `event_id` / `fingerprint` 重放 100% 拒绝；**拒绝机制 = D6 record 端点的 `inbound_event.UNIQUE(fingerprint)` 约束**（写入重复 fingerprint 抛 IntegrityError → 端点返回 409/去重成功）。
- 客户端 fingerprint 写盘（D3）作**第二道闸**（resume 崩溃场景）。

### D5. sessions jsonl 补 `event_source` **+ `event_id`**（修订，回应 U1）
- `runtime-waker-sessions/<id>.jsonl` 每条记录补两个字段：
  - `event_source`（`polling`/`sse`/`replay`）—— 区分 wake 来源路径
  - `event_id`（对齐 `notification.id` / `inbound_event.event_id`）—— **A3 join key**
  - 可选 `fingerprint`（冗余，便于与 `inbound_event.fingerprint` 直查）
- **现状**：`cli/session_wake_log.py:43-52` 当前字段为 `ts/session_id/persona/integration/status/prompt/response_preview/response_chars`，无任何 join key → A3 不可证伪。本项补齐。
- `append_session_wake_log` 签名扩展接受 `event_id` / `event_source`，调用方（`PersonaAgentClient` wake 路径）透传当前 `WakeEvent.event_id` / `source`。

### D6. 服务端「record fingerprint」写入端点 + waker 调用 —— **A1/A2 主闸**（新增，回应 U2）
- **问题**：polling 路径下 waker 主动读 `map todos`，服务端无从「拒绝读」；必须有 waker 在 resume **前**向服务端 record fingerprint 的写入路径作为主闸。
- **机制**：新增服务端写入端点（建议 `POST /me/inbound-events`，agent-scoped，复用 `/me/` auth 模型；或评估复用现有 notification ack 端点——但语义不同，倾向新建以保审计干净），body = `{event_id, event_type, fingerprint, source}`，服务端写入 `inbound_event` 行。
- **去重**：`UNIQUE(fingerprint)` 约束使重复写入直接失败（IntegrityError → 409 Conflict）= A1 重放拒绝 + A2 并发去重的**服务端主闸**。
- **waker 集成**：`cli/runtime_waker.py` 在 `_wake_event` **resume 之前**调用该端点 record 当前 `WakeEvent.fingerprint`；record 成功才 resume，record 返回 409（已存在）则 skip（视为已 wake 过）。
- **与 D3 的关系**：D6（服务端）= 主闸，跨进程/跨重启都生效；D3（客户端写盘）= 第二道闸，仅防本进程 resume 崩溃窗口。两者叠加满足 A1/A2。
- **CLI**：新增 `map --persona <p> inbound-event record --event-id .. --fingerprint ..`（薄包装），waker 经 map_command_client 调用。

## 硬性约束（不可违背）
- hook 路径**只跑 Claude Code CLI**；Cursor SDK / Codex 继续走轮询兜底（钩子能力不对齐，混用引入新耦合）
- 兜底轮询**永不取消**（SSE 多稳都保留作最后一道防线）
- 兜底频率走 `MAP_RUNTIME_INTERVAL`，**Phase 1 不锁死**（Phase 2 实测 SSE 漏事件率 < 0.1% 后再议 15min）

## 验收（断言式测试，全部必须通过）
- **A1 重放拒绝**：服务端层（D6 端点）重放 100 次同 `event_id`/`fingerprint`，0 成功（409），`inbound_event` 只 1 行（→ D4 + D6）
- **A2 并发注入零误唤醒**：同 fingerprint 并发 N 线程调 D6 端点，DB `UNIQUE(fingerprint)` 使仅 1 成功、其余 409；waker 只 resume 一次。**测试设施**：`threading` + 真实 DB UNIQUE 约束（非 mock），新增到 `tests/test_runtime_waker.py`（吸收 reasonable `2751b98f`）
- **A3 审计三段可 join**：`notification.id` ↔ `inbound_event.event_id` ↔ `sessions jsonl.event_id` 三表按 `event_id` join 对齐，无丢失/错位（→ D1 + D5 + D6）
- **A4 P95 仅记基线**（口径明确，吸收 reasonable `d2cdbefa`）：起止 = 事件 `notification.created_at` → waker `_wake_event` resume 返回；按 wake kind（mention / pending_review / topic_lifecycle）分别记；**不设准入门槛**，仅作 Phase 2 对照基线
- **A5 `source` 正确**：Phase 1 所有 `inbound_event.source` = `polling` 且 sessions jsonl `event_source` = `polling`（→ D1 + D5 + D6）

## 回归基线（进 Phase 2 的前置）
- A1–A3、A5 全部断言通过；A4 基线已记录（按 kind）作 Phase 2 对照

## 风险
- `cli/runtime_waker.py` 当前 branch（agent-runtime）已有未提交改动——实施前先 `git commit` checkpoint
- fingerprint 写盘顺序改动影响现有去重逻辑——**先加回归测试固化再改顺序**（D3 方法论）
- D6 新增服务端写入端点扩大攻击面——必须 agent-scoped（`/me/`），复用现有 auth；写入限速防风暴（同 fingerprint 60s 内最多 1 次成功，重复直接 409）

## 实施步骤（running 阶段逐子项推进）
- **I1**：`inbound_event` migration + ORM model，`agent_id` FK（→ D1）
- **I2**：服务端 record 端点（`POST /me/inbound-events`）+ `UNIQUE(fingerprint)` 重放拒绝 + CLI `inbound-event record`（→ D6 + D4）
- **I3**：waker 在 resume 前 record fingerprint（主闸，→ D6）+ 客户端 fingerprint 写盘先于 resume + 回归测试（二闸，→ D3）+ todos 客户端 `created_at` 游标过滤（→ D2）
- **I4**：sessions jsonl 补 `event_source` + `event_id`（→ D5）
- **I5**：验收测试 A1–A5（含 A2 并发真实 DB 设施）+ 按 kind 记录 P95 基线（→ A4）
