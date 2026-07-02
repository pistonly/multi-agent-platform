# Phase 2 实验：waker SSE 叠加 + lifecycle 事件补 publish + 重连补偿

> **本实验 = action_item `706f8039-42c8-4793-9a89-657ddcda82c0` 的交付物**
> （"Phase 2 实验设计：SSE 叠加"，由 host 在 `cfd1578e` resolve 时创建，2026-07-02 14:58 participant 完成 Cursor SDK 验证、前置 Phase 1 全部 acceptance 通过后启动）

## 来源
- 话题：`cfd1578e-dd6f-473a-85c2-a02797718d41`（是否可以将轮询的 waker 改为 hook 的方式）
- 两轮讨论 4 议题全部收敛：①审计表 B `inbound_event`（Phase 1 已落地）②per-persona channel（Phase 1 已核代码是现状）③兜底频率 10min（走 `MAP_RUNTIME_INTERVAL`）④Phase 1 gate（已通过）
- 前置：**Phase 1 实验 `a188555c` 已 `done`**，D1–D6 全部交付、A1–A5 全部通过、124 测试无回归、P95 baseline 已落盘 `.map/generated-plans/phase1-p95-baseline.json`
- participant 在 `3dbbbc44` 完成 Cursor SDK backend 验证，**降级原硬性约束**「hook 路径只跑 Claude Code CLI」→「**统一在 waker 层引入 SSE，三 backend 等价受益；Cursor/Codex 不在 SDK 层订阅 SSE**」

## 目标 / 非目标
- **目标**：在 Phase 1 基础设施之上，把 waker 触发从「轮询 + 兜底」切换到「SSE 长连 + 10min 兜底轮询」；补齐 6 个 lifecycle publish 调用点；验证重连补偿、漏事件补救、重试风暴限速；证明 P95 by kind 改善达到 reviewer 红线
- **非目标**：
  - 取消兜底轮询（兜底是 last-line defense，永久保留）
  - 改动 SSE endpoint 本身（`GET /me/notifications/stream` 是 Phase 1 已核代码的现状）
  - 改动 `inbound_event` schema（Phase 1 已固化；Phase 2 仅写入 `source="sse"` 增量，不改结构）
  - Cursor SDK backend 非 dry-run 实跑（仍需 `CURSOR_API_KEY`，归 v0.8 backlog）

## 交付物（显式，便于 ack 锚定 + reviewer 5 项红线映射）

### D1. SSE 长连主路径替换
- `cli/runtime_waker.py` 新增 `_run_sse_loop`：用 `httpx.stream("GET", "/me/notifications/stream", headers={"Accept": "text/event-stream"}, timeout=None)` 维护长连，解析 `event: notification\ndata: {...}` 帧
- 主循环切到 `_run_sse_loop`：每条 SSE 事件触发 `_wake_event`（复用 Phase 1 I3 实现的 record→mark→resume 流程，**不重复实现**）
- 长连生命周期：connect → consume → on disconnect 触发 D3 重连补偿
- **`event_id` 复用 Phase 1 I4 的 UUID5**：`uuid5(NAMESPACE, f"{agent_id}:{fingerprint}")`，sessions jsonl / `inbound_event.event_id` 三段 join 不需要重写
- `inbound_event.source` 写入 `"sse"`（Phase 1 enum 已扩展，仅写值变化）

### D2. 6 个 lifecycle publish 调用点补齐
当前 SSE 只覆盖 `mention` / `open_topic_opportunity`（走 `notification_service._emit_created`）。需补：
| wake kind | 当前 publish 覆盖 | Phase 2 改动点 |
|---|---|---|
| `pending_topic_reply` / `topic_lifecycle` | ❌ | 在 `topic_service` 创建/状态变化路径加 `_emit_created`（与 mention 同款） |
| `experiment_lifecycle` / `pending_review` / `pending_result_review` / `addressed_review_item` | ❌ | 在 `experiment_service` 状态变化 + `review_service` 创建/状态路径加 `_emit_created` |

- 每处补 publish 必须沿用 `notification_service._emit_created` 入口（**不绕过 service 直接调 `notification_stream.publish`**，避免审计分裂）
- `recipient_agent_id` 必须按规则取对应 persona agent（host / participant / reviewer），不能用 topic creator 或 experiment creator 替代
- 事件 payload 至少含 `topic_id` / `experiment_id` / `kind`，便于 waker 端 fingerprint 拼接

### D3. 重连补偿（指数退避 + 漏事件补救）
- SSE 断连（任意异常 / EOF / 超时）触发指数退避：1s → 2s → 4s → 8s → 16s → 30s（上限 30s）
- 重连成功后**先做一次全量 todos sync** + `list_notifications(unread_only=true)` 补漏，再消费增量事件流
- 退避状态写 `.map/runtime-waker-state-<persona>.json` 续期字段 `sse_backoff_until`，进程重启不丢退避位置
- **服务端不需要做事件序号**：Phase 1 D6 的 `inbound_event.UNIQUE(fingerprint)` 已经是终态闸门，重投一律 409 → waker skip resume；服务端补漏靠 `unread_only=true` 全量回放
- **不做** last-event-id 协议（Phase 1 决定 4：避免 SSE endpoint 复杂度膨胀，漏事件靠客户端补读）

### D4. 重试风暴限速（client-side 同 fingerprint 60s 内最多 1 次 resume 尝试）
- waker 进程内维护 `recent_resume_attempts: dict[fingerprint, last_attempt_ts]`，60s 内同 fingerprint 直接 skip（不调用服务端 record、不调用 backend.wake）
- **服务端闸门仍生效**：`inbound_event.UNIQUE(fingerprint)` 防跨进程重投；client-side 闸门减少不必要的服务端调用
- 与 D3 关系：D4 是「正常事件流」下避免重复的轻量闸门，D3 服务端 UNIQUE 是「崩溃恢复 / 重连补偿」下的硬闸门

### D5. backend 边界约束降级（采纳 participant 3dbbbc44 验证结论）
- **删除**原硬性约束「hook 路径只跑 Claude Code CLI；Cursor SDK / Codex 继续走轮询兜底」
- 替换为：「**SSE 订阅发生在 waker 进程层**（普通 Python 进程，与 backend 无关），三 backend 等价受益；Cursor/Codex 不在 SDK agent 层订阅 SSE —— per-wake 启停的 agent 不需要持续 SSE 连接」
- 文档落地：`.cursor/skills/map-runtime-waker/SKILL.md` 与 `docs/MAP-RUNTIME-WAKER.md` 同步更新；删掉「仅 Claude Code CLI」措辞
- 本实验**不实施**真实 Cursor backend 非 dry-run smoke test（需 `CURSOR_API_KEY`，归 v0.8）；保留 `--backend cursor --once --dry-run` 已通过的现状

### D6. 兜底轮询保留（10min，最后一道防线）
- `MAP_RUNTIME_INTERVAL=600`（默认 10min，Phase 1 不锁死，Phase 2 暂保留）
- SSE 长连是主路径；兜底仅在以下场景触发：(a) 启动时第一次拉取；(b) SSE 断连重连成功后的全量补读；(c) 兜底周期到点的 sanity check
- sessions jsonl 中兜底轮询触发的 wake，`event_source="polling"`（不变），SSE 触发的为 `"sse"`，重连补读触发的为 `"replay"`

## 硬性约束（不可违背）
- **兜底轮询永不取消**：SSE 多稳都保留作最后一道防线
- **重连退避上限 30s**：禁止 > 30s，否则事件延迟退化
- **服务端闸门优先**：所有重连补偿路径仍受 `inbound_event.UNIQUE(fingerprint)` 终态保护
- **Phase 1 五项 A 不变式必须保持**：A1 重放拒绝 / A2 并发零误唤醒 / A3 三段 join / A4 P95 基线对照 / A5 `source` 字段值（Phase 1 = polling，Phase 2 加 `"sse"` 增量）

## 验收（断言式测试，全部必须通过）

| 编号 | 指标 | 当前基线（Phase 1） | 目标（Phase 2） | 怎么测 |
|---|---|---|---|---|
| **A1** | P95 wake latency by kind | mention ≈ 0.0003s, pending_review ≈ 0.0003s, topic_lifecycle ≈ 0.0002s（jsonl append only） | mention < 5s, pending_review < 10s, topic_lifecycle < 30s（端到端） | 注入合成 notification → 测 `notification.created_at` → waker `_wake_event` resume 返回 |
| **A2** | 漏事件率 | 0（polling 天然） | 0（SSE 断连后 unread_only=true 补漏） | 模拟 SSE 断连 30s / 5min / 30min 三档，断言恢复后无 wake 丢失 |
| **A3** | 空轮询比例 | n/a（Phase 1 是 100% 轮询） | > 95%（兜底触发后 todos 大多为空） | 跑 1h，统计兜底 tick 中「拉到空 todos」占比 |
| **A4** | 重复唤醒率（同 fingerprint） | < 0.1%（Phase 1 UNIQUE 闸门） | < 0.1%（SSE 重投 + 兜底轮询叠加去重） | 注入重复 event_id 1000 次 + 模拟 SSE 断连重投，断言实际 resume ≤ 1 次 |
| **A5** | 幂等写成功率 | 100%（Phase 1 UNIQUE 主闸 + 客户端二闸） | 100%（D3 重连补偿 + D4 client-side 限速叠加后仍保持） | 服务端层重放 100 次同 fingerprint，0 成功（409），`inbound_event` 只 1 行 |
| **A6** | 审计三段可 join | 三段等值 join（Phase 1） | 三段等值 join 仍可用，且 `inbound_event.source` 涵盖 `polling` 和 `sse` | 跑 Phase 1 的 `test_waker_phase1_acceptance.py` 全部 6 条；新增 1 条 SSE 路径 join 测试 |
| **A7** | sessions jsonl `event_source` 字段值 | 全部 `"polling"`（Phase 1） | 出现 `"sse"` 和 `"replay"`（与 `"polling"` 并存），无空值 | 跑混合流量，断言字段值集合 ⊆ `{"polling", "sse", "replay"}` 且 ≥ 2 种 |

## 回归基线
- Phase 1 全部 6 个验收测试（`tests/test_waker_phase1_acceptance.py`）继续通过
- Phase 1 124 测试无回归
- 端到端 smoke：`map --persona host status`、`map --persona host todos`、`./scripts/start-all-wakers.sh` 启动 + 杀进程 + 重启幂等

## 风险
- SSE 长连阻塞主线程：必须用独立线程或 asyncio task；现有 `_run_forever_sync` 改 `_run_sse_loop` 需重构线程模型
- 重连风暴：服务端 100+ waker 进程同时断连时重连可能撞服务器；缓解靠退避 + 上限 30s
- SSE endpoint 协议变化：本实验不修改 `server/api/agents.py:189` 的 SSE 实现，仅消费；如服务端协议改动需另起实验
- `inbound_event` 表增长：Phase 2 SSE 触发更频繁，按 D1 写入 `source="sse"` 会比 Phase 1 仅 `"polling"` 多 ~10×；当前无分区 / TTL，靠 `acked_at` 后续单独处理（v0.8 backlog）
- 端到端 P95 受 Claude Code CLI 启动时间主导（实测 3–8s）；mention < 5s 目标在 CLI backend 严格达成需 warm-pool 或缩短启动（v0.8 backlog）

## 实施步骤（running 阶段逐子项推进）

- **I1**：补齐 6 个 lifecycle publish 调用点（D2）—— `topic_service` + `experiment_service` + `review_service` 各自加 `_emit_created` 入口；recipient 解析规则；events.jsonl 测试断言
- **I2**：waker `_run_sse_loop` 实现（D1）—— httpx stream + 帧解析 + 重连退避（D3）+ 全量 todos sync + unread_only=true 补漏；保留兜底 10min 轮询（D6）；sessions jsonl `event_source="sse"|"polling"|"replay"`
- **I3**：client-side 重试限速（D4）—— `recent_resume_attempts` dict + 60s 滑动窗口；与 D1 主路径集成
- **I4**：文档同步（D5）—— `.cursor/skills/map-runtime-waker/SKILL.md` + `docs/MAP-RUNTIME-WAKER.md` 删掉「仅 Claude Code CLI」措辞，更新为「waker 层统一引入 SSE」
- **I5**：验收测试（A1–A7）—— 在 `tests/test_waker_phase2_acceptance.py` 新增：P95 端到端注入、SSE 断连 30s/5min/30min 三档补漏、空轮询比例 1h 实测、event_id 重投 1000 次、unread_only 补漏后 sessions jsonl 三段 join
- **I6**：结果整理 —— 与 Phase 1 P95 baseline 对照；更新 `docs/MAP-RUNTIME-WAKER.md` 上线 checklist；reviewer 提请评审

## 实施纪律
- I1 必须先于 I2：没有 lifecycle publish 补齐，SSE 长连也收不到事件，I2 测试无意义
- I3 与 I2 并行实现但不阻塞 I5 验收：A4 仍由服务端 UNIQUE 主闸保护，D4 client-side 是优化项
- I4 文档改动必须与 I2 代码 commit 同 PR，便于 reviewer diff 对照
- Phase 1 已落地的 `inbound_event` / D6 record 端点 / sessions jsonl 字段**不改不重构**，仅消费；如需重构另起实验
