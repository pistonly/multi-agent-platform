# Phase 2 实验：waker SSE 叠加 + lifecycle 事件补 publish + 重连补偿（plan v2）

> **v2 变更**：回应 reviewer 评审（review `6d1c1dfa` + review `1fb7eac4`）的 5 项 open unreasonable。
>
> | 评审项 | 修订 |
> |---|---|
> | **U1 `43000a53` (P95 红线出处不明)** | A1 行显式声明红线 = reviewer Round 1 `bf3f263d` 立场表 + Round 1 Summary `3f9d80cd` 共识 7；引入 Phase 1 baseline 对照公式 |
> | **U2 `bee5cd91` (D4 与 D3 补漏冲突)** | 选 (b)：补漏事件携带 `replay=True` 豁免标记，强制 wake，绕开 D4 client-side 60s 限速；服务端 UNIQUE 主闸仍生效防跨进程重投。落地 I2 |
> | **U3 `3c52a2ad` (A3 1h 实测条件不明)** | 明确：双档受控流量（N=10 / 小时 + 空载基线）、启动 10min + SSE 重连恢复期剔除、不达标归因（真实事件 vs 兜底逻辑错）拆 3 段报表 |
> | **U4 `f531a98f` (A3 缺边界条件)** | 拆「启动期」与「稳态期」两段独立断言；SSE 重连恢复期（断连后全量补读窗口内）不计入空轮询分母 |
> | **U5 `b07d68a9` (A1 mention <5s 与 CLI 启动 3–8s 混在一起)** | 拆 A1 为 A1a（SSE 通知到达 waker 进程延迟 < 1s，硬门槛，与 backend 无关）+ A1b（backend.wake→resume 延迟，观测项，CLI backend 实测基线 ~3–8s 不在本实验门槛，归 v0.8 warm-pool） |

## 来源
- 话题：`cfd1578e-dd6f-473a-85c2-a02797718d41`（是否可以将轮询的 waker 改为 hook 的方式）
- 两轮讨论 4 议题全部收敛：①审计表 B `inbound_event`（Phase 1 已落地）②per-persona channel（Phase 1 已核代码是现状）③兜底频率 10min（走 `MAP_RUNTIME_INTERVAL`，Round 2 Summary `b3ac6b66`）④Phase 1 gate（已通过，Round 2 Summary `b3ac6b66`）
- **reviewer 红线出处（回应 U1）**：Round 1 reviewer 立场 `bf3f263d` 表「一、可证伪验收指标」明列 P95 by kind 阈值（mention < 5s / pending_review < 10s / topic_lifecycle < 30s）+ 漏事件率 0 / 空轮询 > 95% / 重复唤醒率 < 0.1% / 幂等 100%；Round 1 Summary `3f9d80cd` 共识 7 写明「5 项可证伪指标（**reviewer 红线，缺一不可**）」作为实验通过硬条件。本实验 A1/A3/A4/A5 全部对这 5 项做断言式测试。
- 前置：**Phase 1 实验 `a188555c` 已 `done`**，D1–D6 全部交付、A1–A5 全部通过、83 测试无回归、P95 baseline 已落盘 `.map/generated-plans/phase1-p95-baseline.json`（jsonl append only，mention p95 ≈ 0.0009s；Phase 2 A1 端到端测量，**口径不同**，对照为 informational）
- participant 在 `3dbbbc44` 完成 Cursor SDK backend 验证，**降级原硬性约束**「hook 路径只跑 Claude Code CLI」→「**统一在 waker 层引入 SSE，三 backend 等价受益；Cursor/Codex 不在 SDK 层订阅 SSE**」

## 目标 / 非目标
- **目标**：在 Phase 1 基础设施之上，把 waker 触发从「轮询 + 兜底」切换到「SSE 长连 + 10min 兜底轮询」；补齐 6 个 lifecycle publish 调用点；验证重连补偿、漏事件补救、重试风暴限速；证明 P95 by kind 改善达到 reviewer 红线（见 A1 对照公式）
- **非目标**：
  - 取消兜底轮询（兜底是 last-line defense，永久保留）
  - 改动 SSE endpoint 本身（`GET /me/notifications/stream` 是 Phase 1 已核代码的现状）
  - 改动 `inbound_event` schema（Phase 1 已固化；Phase 2 仅写入 `source="sse"` 增量，不改结构）
  - Cursor SDK backend 非 dry-run 实跑（仍需 `CURSOR_API_KEY`，归 v0.8 backlog）
  - CLI backend 启动延迟优化（warm-pool 归 v0.8；本实验 A1b 仅观测不设门槛）

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

### D4. 重试风暴限速（client-side 同 fingerprint 60s 内最多 1 次 resume 尝试）+ **补漏豁免（回应 U2）**
- waker 进程内维护 `recent_resume_attempts: dict[fingerprint, last_attempt_ts]`，60s 内同 fingerprint 直接 skip（不调用服务端 record、不调用 backend.wake）
- **服务端闸门仍生效**：`inbound_event.UNIQUE(fingerprint)` 防跨进程重投；client-side 闸门减少不必要的服务端调用
- 与 D3 关系：D4 是「正常事件流」下避免重复的轻量闸门，D3 服务端 UNIQUE 是「崩溃恢复 / 重连补偿」下的硬闸门
- **补漏豁免机制（回应 U2 `bee5cd91`）**：D3 重连后 `unread_only=true` 全量补漏阶段拉到的事件，**强制跳过 D4 client-side 限速**。实现：
  - 补漏事件由 `unread_only=true` 拉到时即设 `event_source="replay"`（与 SSE 长连事件区分，sessions jsonl 已能区分）
  - `_wake_event` 入口检查 `event_source == "replay"` → **跳过 `recent_resume_attempts` 检查**（D4 闸门豁免），直接进入 record→mark→resume 流程
  - 服务端 `inbound_event.UNIQUE(fingerprint)` 主闸**仍生效**：补漏事件若此前已被别处 resume 过，则 409 → waker skip（与正常事件流同款处理）
  - **与 D4 关系**：D4 仅约束「正常 SSE 长连 + 兜底 polling」两条路径的重复；D3 补漏走豁免，避免 reviewer 指出的「补漏期间拉到 >1 条/60s 事件全数 skip → A2 漏事件率 = 0 不可达成」
  - **落地 I2**：`cli/runtime_waker.py:_wake_event` 开头增加 `if event_source == "replay": bypass_d4 = True`；D4 闸门函数 `_should_skip_due_to_rate_limit(fingerprint)` 检测该标记直接 return False

### D5. backend 边界约束降级（采纳 participant 3dbbbc44 验证结论）
- **删除**原硬性约束「hook 路径只跑 Claude Code CLI；Cursor SDK / Codex 继续走轮询兜底」
- 替换为：「**SSE 订阅发生在 waker 进程层**（普通 Python 进程，与 backend 无关），三 backend 等价受益；Cursor/Codex 不在 SDK agent 层订阅 SSE —— per-wake 启停的 agent 不需要持续 SSE 连接」
- 文档落地：`.cursor/skills/map-runtime-waker/SKILL.md` 与 `docs/MAP-RUNTIME-WAKER.md` 同步更新；删掉「仅 Claude Code CLI」措辞
- 本实验**不实施**真实 Cursor backend 非 dry-run smoke test（需 `CURSOR_API_KEY`，归 v0.8）；保留 `--backend cursor --once --dry-run` 已通过的现状

### D6. 兜底轮询保留（10min，最后一道防线）
- `MAP_RUNTIME_INTERVAL=600`（默认 10min，Round 2 Summary `b3ac6b66` 共识，Phase 1 不锁死，Phase 2 暂保留）
- SSE 长连是主路径；兜底仅在以下场景触发：(a) 启动时第一次拉取；(b) SSE 断连重连成功后的全量补读；(c) 兜底周期到点的 sanity check
- sessions jsonl 中兜底轮询触发的 wake，`event_source="polling"`（不变），SSE 触发的为 `"sse"`，重连补读触发的为 `"replay"`

## 硬性约束（不可违背）
- **兜底轮询永不取消**：SSE 多稳都保留作最后一道防线
- **重连退避上限 30s**：禁止 > 30s，否则事件延迟退化
- **服务端闸门优先**：所有重连补偿路径仍受 `inbound_event.UNIQUE(fingerprint)` 终态保护
- **Phase 1 五项 A 不变式必须保持**：A1 重放拒绝 / A2 并发零误唤醒 / A3 三段 join / A4 P95 基线对照 / A5 `source` 字段值（Phase 1 = polling，Phase 2 加 `"sse"` 增量）
- **A1 红线 = reviewer 硬锚点**：来自 `bf3f263d` 立场表 + Round 1 Summary `3f9d80cd` 共识 7；非 host 私定（回应 U1）

## 验收（断言式测试，全部必须通过）

> **关于口径（回应 U1 `43000a53`）**：
> - Phase 1 P95 baseline (`phase1-p95-baseline.json`) 是 **jsonl append only 测量**（mention p95 ≈ 0.0009s），不包含 SSE 传输 + backend.wake resume；与 Phase 2 端到端测量**口径不同**
> - **Phase 2 A1 红线来自 reviewer 立场 `bf3f263d` + Round 1 Summary `3f9d80cd` 共识 7**，非 host 私定；Phase 1 baseline 仅作 informational 对照
> - 对照公式：`Phase 2 端到端 P95 ≤ reviewer 红线`（硬门槛），同时 `Phase 2 端到端 P95 ≪ Phase 1 60s 轮询间隔`（informational）

| 编号 | 指标 | Phase 1 基线（informational） | Phase 2 目标（reviewer 红线 + 拆分） | 怎么测 |
|---|---|---|---|---|
| **A1a** | **SSE 通知到达 waker 进程延迟** | n/a（polling 路径无此环节） | **mention < 1s, pending_review < 1s, topic_lifecycle < 1s**（reviewer 红线的传输段，与 backend 无关） | 注入合成 notification → 服务端 `_emit_created` 时间戳 → waker SSE 帧 `_handle_sse_event` 接收时间戳 → Δt < 1s |
| **A1b** | **backend.wake 调用到 Claude Code CLI 完成 resume 延迟**（观测项，**不在本实验门槛**） | n/a | 观测基线（CLI 启动实测 ~3–8s），warm-pool 优化归 v0.8 | 注入合成 notification → waker `_wake_event` resume 返回 → 记录 A1b；本实验仅产出 baseline，无准入门槛 |
| **A1 总** | 端到端 P95 by kind | mention ≈ 0.0009s（jsonl append only，**不同口径**） | mention < 5s, pending_review < 10s, topic_lifecycle < 30s（reviewer 红线，硬门槛） | 注入合成 notification → `notification.created_at` → waker `_wake_event` resume 返回；**端到端** P95 必须满足 reviewer 红线；A1a + A1b 拆分后瓶颈归属清晰 |
| **A2** | 漏事件率 | 0（polling 天然） | 0（SSE 断连后 `unread_only=true` 补漏，D4 豁免见 D4） | 模拟 SSE 断连 30s / 5min / 30min 三档，断言恢复后无 wake 丢失；补漏事件 `event_source="replay"` 验证 sessions jsonl 可区分 |
| **A3** | 空轮询比例（**双档受控流量 + 边界分段**，回应 U3 `3c52a2ad` + U4 `f531a98f`） | n/a（Phase 1 是 100% 轮询） | **稳态期** ≥ 95%；**启动期**（前 10min）不约束；**SSE 故障恢复期**（断连后全量补读窗口内，不计入分母） | 双档测试：**(a) 受控流量 N=10 wake 事件/小时**（注入受控）；**(b) 空载基线**（无外部注入）。每档拆 3 段报表：(i) 启动 0–10min；(ii) 稳态 10–60min；(iii) SSE 重连恢复期（断连触发后到 todos sync 完成，期间不算分母）。不达标归因：报表 (i) 不达标 → 启动逻辑错；报表 (ii) 不达标 → 兜底逻辑错；报表 (iii) 异常 → SSE 重连补偿 bug |
| **A4** | 重复唤醒率（同 fingerprint） | < 0.1%（Phase 1 UNIQUE 闸门） | < 0.1%（SSE 重投 + 兜底轮询 + D4 限速 + D4 补漏豁免叠加后仍保持） | 注入重复 event_id 1000 次 + 模拟 SSE 断连重投 + 模拟补漏路径；断言实际 resume ≤ 1 次 |
| **A5** | 幂等写成功率 | 100%（Phase 1 UNIQUE 主闸 + 客户端二闸） | 100%（D3 重连补偿 + D4 client-side 限速 + D4 补漏豁免叠加后仍保持；服务端 UNIQUE 主闸不受豁免影响） | 服务端层重放 100 次同 fingerprint，0 成功（409），`inbound_event` 只 1 行；补漏路径另跑 50 次同 fingerprint，0 成功 |
| **A6** | 审计三段可 join | 三段等值 join（Phase 1） | 三段等值 join 仍可用，且 `inbound_event.source` 涵盖 `polling` 和 `sse` | 跑 Phase 1 的 `test_waker_phase1_acceptance.py` 全部 6 条；新增 1 条 SSE 路径 join 测试 |
| **A7** | sessions jsonl `event_source` 字段值 | 全部 `"polling"`（Phase 1） | 出现 `"sse"` 和 `"replay"`（与 `"polling"` 并存），无空值 | 跑混合流量，断言字段值集合 ⊆ `{"polling", "sse", "replay"}` 且 ≥ 2 种；补漏事件 sessions jsonl 必填 `"replay"` |

## 回归基线
- Phase 1 全部 6 个验收测试（`tests/test_waker_phase1_acceptance.py`）继续通过
- Phase 1 83 测试无回归
- 端到端 smoke：`map --persona host status`、`map --persona host todos`、`./scripts/start-all-wakers.sh` 启动 + 杀进程 + 重启幂等

## 风险
- SSE 长连阻塞主线程：必须用独立线程或 asyncio task；现有 `_run_forever_sync` 改 `_run_sse_loop` 需重构线程模型
- 重连风暴：服务端 100+ waker 进程同时断连时重连可能撞服务器；缓解靠退避 + 上限 30s
- SSE endpoint 协议变化：本实验不修改 `server/api/agents.py:189` 的 SSE 实现，仅消费；如服务端协议改动需另起实验
- `inbound_event` 表增长：Phase 2 SSE 触发更频繁，按 D1 写入 `source="sse"` 会比 Phase 1 仅 `"polling"` 多 ~10×；当前无分区 / TTL，靠 `acked_at` 后续单独处理（v0.8 backlog）
- 端到端 P95 受 Claude Code CLI 启动时间主导（实测 3–8s）：**A1 已拆分为 A1a（SSE 传输硬门槛 < 1s）+ A1b（CLI 启动观测项，不设门槛）**，瓶颈归属清晰；CLI backend 严格达成 A1 总 < 5s 需 warm-pool（v0.8 backlog）；A1 红线对 non-CLI backend（Cursor/Codex dry-run 等）严格达成
- D4 补漏豁免窗口被滥用：补漏豁免仅限 `event_source="replay"` 路径（来自 `unread_only=true` 全量补读），不会扩散到正常 SSE 长连 + 兜底 polling 两条路径；服务端 UNIQUE 主闸仍防跨进程重投；豁免滥用风险已通过标记 + 双闸分层兜住

## 实施步骤（running 阶段逐子项推进）

- **I1**：补齐 6 个 lifecycle publish 调用点（D2）—— `topic_service` + `experiment_service` + `review_service` 各自加 `_emit_created` 入口；recipient 解析规则；events.jsonl 测试断言
- **I2**：waker `_run_sse_loop` 实现（D1 + D3 + D4 补漏豁免）—— httpx stream + 帧解析 + 重连退避（D3）+ 全量 todos sync + `unread_only=true` 补漏（**补漏事件强制 `event_source="replay"`，绕过 D4 client-side 限速，服务端 UNIQUE 主闸仍生效**，落地 D4 豁免机制）+ 保留兜底 10min 轮询（D6）；sessions jsonl `event_source="sse"|"polling"|"replay"`
- **I3**：client-side 重试限速（D4）—— `recent_resume_attempts` dict + 60s 滑动窗口；**与 I2 集成时增加 `event_source="replay"` 豁免分支**（参考 D4 补漏豁免机制）；与 D1 主路径集成
- **I4**：文档同步（D5）—— `.cursor/skills/map-runtime-waker/SKILL.md` + `docs/MAP-RUNTIME-WAKER.md` 删掉「仅 Claude Code CLI」措辞，更新为「waker 层统一引入 SSE」
- **I5**：验收测试（A1a / A1b / A1 总 / A2 / A3 / A4 / A5 / A6 / A7）—— 在 `tests/test_waker_phase2_acceptance.py` 新增：
  - A1a：SSE 传输延迟端到端注入测（与 backend 无关）
  - A1b：CLI backend 启动延迟观测项（仅产出 baseline，无准入门槛）
  - A1 总：端到端 P95 注入测 + 与 reviewer 红线（mention < 5s 等）硬比对
  - A2：SSE 断连 30s / 5min / 30min 三档补漏 + 验证 sessions jsonl `event_source="replay"` 出现
  - A3：**双档受控流量（N=10 + 空载基线）+ 3 段报表（启动 0–10min / 稳态 10–60min / SSE 恢复期不算分母）+ 不达标归因**
  - A4：event_id 重投 1000 次（含正常 SSE + 兜底 polling + D4 豁免的补漏路径）
  - A5：服务端层重放 100 次同 fingerprint（含补漏路径 50 次）
  - A6：SSE 路径 join 测试
  - A7：sessions jsonl `event_source` 字段值集合 ⊆ `{"polling", "sse", "replay"}` 且 ≥ 2 种
- **I6**：结果整理 —— 与 Phase 1 P95 baseline informational 对照；A1a / A1b 拆分报告；更新 `docs/MAP-RUNTIME-WAKER.md` 上线 checklist；reviewer 提请评审

## 实施纪律
- I1 必须先于 I2：没有 lifecycle publish 补齐，SSE 长连也收不到事件，I2 测试无意义
- I3 与 I2 并行实现但不阻塞 I5 验收：A4 仍由服务端 UNIQUE 主闸保护，D4 client-side 是优化项；D4 补漏豁免是 I2 的必备分支（响应 U2）
- I4 文档改动必须与 I2 代码 commit 同 PR，便于 reviewer diff 对照
- Phase 1 已落地的 `inbound_event` / D6 record 端点 / sessions jsonl 字段**不改不重构**，仅消费；如需重构另起实验
- **A1 拆分纪律**：result review 时 reviewer 必须能清楚看到 A1a（A1 总的传输瓶颈）vs A1b（CLI 启动瓶颈）独立数据；不能合并报数，避免「Phase 2 跑完 P95 ≈ 4s 但 CLI 启动 3.5s」时无法判定通过
