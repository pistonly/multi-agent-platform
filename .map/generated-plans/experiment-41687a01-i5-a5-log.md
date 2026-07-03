# I5-A5 执行日志：幂等写成功率 100%（client-side + server-side UNIQUE 集成）

> 实验：`41687a01-3992-471b-b415-8ad80f732f80` — waker Phase 2：SSE 叠加 + lifecycle 事件补 publish + 重连补偿
> 当前 plan version：2；I1–I5-A4 已完成；本日志覆盖 **I5-A5**。

## 范围与目标

Plan §A5 — reviewer 红线：

| 指标 | Phase 1 | Phase 2 目标 | 怎么测 |
|---|---|---|---|
| 幂等写成功率 | 100%（UNIQUE 主闸 + 客户端二闸） | 100%（D3 重连补偿 + D4 client-side 限速 + D4 补漏豁免叠加后仍保持；**服务端 UNIQUE 主闸不受豁免影响**） | 服务端层重放 100 次同 fingerprint，0 成功（409），`inbound_event` 只 1 行；补漏路径另跑 50 次同 fingerprint，0 成功 |

服务端层 100 + 50 两条断言已在 `tests/test_waker_phase2_acceptance.py:test_a5_replay_rejection_holds_across_sources` + `test_a4_replay_replay_path_only_one_row` 覆盖（plan §A5 「怎么测」原文）。本日志聚焦 **client-side pipeline 如何响应 server 409 + client-side gate 与 server-side UNIQUE 的协作** —— 即 reviewer 红线「服务端 UNIQUE 主闸不受豁免影响」的 client 端表现。

## 改动清单

| 文件 | 变更 |
|------|------|
| `tests/test_waker_phase2_e2e_a5.py`（NEW） | 8 个 e2e 测试覆盖 client+server 集成幂等性 + server 409 处理 + 跨路径 stack + regression guard + baseline emitter |

合计：1 文件新增，+520 行。

## 设计要点

### 1. Stub 客户端可控 server 行为

新增 `_StubMapClient.server_first_sighting: bool` 参数控制服务端 UNIQUE 闸门行为：

- `server_first_sighting=True`（默认）：模拟正常服务端，首见 fingerprint 返回 True，重复返回 False（与真实 `inbound_event.UNIQUE(fingerprint)` 行为一致）
- `server_first_sighting=False`：模拟服务端拒绝所有同 fingerprint —— 用于验证 reviewer 红线「服务端 UNIQUE 主闸不受豁免影响」

`_seen_fingerprints` set 跟踪已声明 fingerprint，确保第二次同 fingerprint 返回 False（即使不传 `--force`）。

### 2. 7 个 e2e 测试覆盖 reviewer 红线

| 测试 | 场景 | 期望 wake_async | 闸门 |
|------|------|----------------|------|
| `test_a5_sse_storm_100_same_fingerprint_yields_one_wake` | 100 SSE 同 fingerprint | ≤ 1 | D4 (60s) 主 + TTL (30min) 备 |
| `test_a5_replay_50_same_fingerprint_yields_one_wake` | 50 replay 同 fingerprint | = 1 | TTL alone（replay bypasses D4） |
| `test_a5_server_first_call_rejects_no_wake` | server 拒绝首呼 | = 0 | server UNIQUE 409（无 prior claim） |
| `test_a5_server_first_call_rejects_replay_path_no_wake` | replay 路径 + server 拒绝 | = 0 | TTL × 4 + server UNIQUE × 1 |
| `test_a5_server_first_call_accepts_then_client_gates_protect` | server 接受首呼 + 100 SSE 同 fp | = 1 | server accept → D4 + TTL stop the rest |
| `test_a5_cross_source_mix_same_fingerprint_yields_one_wake` | SSE → replay → polling 三路径同 fp | = 1 | TTL (SSE→replay) + polling `_should_skip_event` |
| `test_a5_distinct_fingerprints_via_replay_all_wake` | 5 distinct fp via replay | = 5 | A2 regression guard |
| `test_a5_baseline_emitted` | baseline JSON | n/a | I6 收口 |

### 3. Test 3/4 验证 reviewer 关键 invariant：「服务端 UNIQUE 不受豁免影响」

`test_a5_server_first_call_rejects_no_wake` + `test_a5_server_first_call_rejects_replay_path_no_wake` 是 plan §A5 红线「服务端 UNIQUE 主闸不受豁免影响」的端到端落地：

- **正常 SSE 路径**：TTL/D4 闸门绕过 + server 拒绝 → 0 wake_async（即使没有 client-side gate，server UNIQUE 仍兜住）
- **replay 路径（D4 bypass）**：TTL 闸门正常 + server 拒绝首呼 → 0 wake_async；后续 4 个 replay iteration 被 TTL 闸门拦下，server 只看到 1 个 record_call

这是 review 时 reviewer 会问的关键问题：「如果 D4 补漏豁免让 replay 路径绕过 client-side 限速，服务端还能保证幂等吗？」—— 本测试明确回答：**能**，server UNIQUE 是终态闸门，不受任何 client-side 豁免影响。

### 4. 与 A4 测试的分工

| 维度 | A4 | A5 |
|------|----|----|
| 焦点 | client-side dedup stack（D4 + TTL gate 在 `_wake_event` 入口） | server-side UNIQUE gate + client-side 协作 |
| stub server 行为 | `inbound_event_record` 永远 True | 可控 True/False（验证 server 409 路径） |
| 关键 invariant | 同 fingerprint ≤ 1 wake_async | server 拒绝首呼时 0 wake_async + replay 路径下 server UNIQUE 仍生效 |
| 关联 plan 段 | D3 重连补偿 + D4 client-side 限速 + TTL 修复 | A5 幂等写成功率 + 「服务端 UNIQUE 不受豁免影响」 |

### 5. 跨路径 stack 测试（Test 5）

`test_a5_cross_source_mix_same_fingerprint_yields_one_wake` 跑 SSE → replay → polling 三路径同一 fingerprint：

| 路径 | 闸门 | 期望 wake_async 增量 |
|------|------|---------------------|
| SSE 长连实时帧 | TTL 允许首呼 | +1 |
| Replay 重连补漏 | TTL 拦（同 fingerprint 已在 woken 状态） | +0 |
| Polling 兜底 | `_should_skip_event` 拦（persona_inflight + TTL） | +0 |
| **合计** | | **= 1** |

Server 端 `record_calls` 只有 1 次（client-side gate 拦在 server round-trip 之前），符合 plan §A5 「inbound_event 只 1 行」要求。

### 6. Baseline JSON

写入 `tmp_path/phase2-a5-baseline.json`，包含：

```json
{
  "produced_at": "phase2-i5-a5",
  "metric": "幂等写成功率 (client-side TTL/D4 + server-side UNIQUE 集成)",
  "redline": "≤ 1 wake per fingerprint (100% idempotency)",
  "server_side_redline": "服务端层重放 100 次同 fingerprint → 1 + 99; 补漏路径 50 次 → 1 + 49. covered by test_waker_phase2_acceptance.py",
  "scenarios": [...]
}
```

canonical baseline `.map/generated-plans/phase2-p95-baseline.json` 留到 I6 收口时与 A1a/A1b/A1总/A2/A3/A4/A5/A6/A7 一起汇总。

## 测试结果

```text
$ pytest tests/test_waker_phase2_e2e_a5.py -v

tests/test_waker_phase2_e2e_a5.py::test_a5_sse_storm_100_same_fingerprint_yields_one_wake PASSED [ 12%]
tests/test_waker_phase2_e2e_a5.py::test_a5_replay_50_same_fingerprint_yields_one_wake PASSED [ 25%]
tests/test_waker_phase2_e2e_a5.py::test_a5_server_first_call_rejects_no_wake PASSED [ 37%]
tests/test_waker_phase2_e2e_a5.py::test_a5_server_first_call_rejects_replay_path_no_wake PASSED [ 50%]
tests/test_waker_phase2_e2e_a5.py::test_a5_server_first_call_accepts_then_client_gates_protect PASSED [ 62%]
tests/test_waker_phase2_e2e_a5.py::test_a5_cross_source_mix_same_fingerprint_yields_one_wake PASSED [ 75%]
tests/test_waker_phase2_e2e_a5.py::test_a5_distinct_fingerprints_via_replay_all_wake PASSED [ 87%]
tests/test_waker_phase2_e2e_a5.py::test_a5_baseline_emitted PASSED [100%]

============================== 8 passed in 0.15s ==============================
```

完整 Phase 1 + Phase 2 套件（107 unique tests，`test_waker_phase2_acceptance.py` 被列两次 = 114）：

```text
$ pytest tests/test_waker_phase2_e2e_a5.py tests/test_waker_phase2_e2e_a4.py \
         tests/test_waker_phase2_e2e_a3.py tests/test_waker_phase2_e2e_a2.py \
         tests/test_waker_phase2_e2e_a1a.py tests/test_waker_phase2_e2e_a1b.py \
         tests/test_waker_phase2_e2e_a1_total.py tests/test_waker_phase2_i2_i3.py \
         tests/test_waker_phase2_acceptance.py tests/test_waker_phase2_sse_consumer.py \
         tests/test_waker_phase2_i1.py tests/test_waker_phase1_acceptance.py \
         tests/test_waker_phase2_acceptance.py -q --no-header

........................................................................ [ 63%]
..........................................                               [ 93%]
114 passed in 379.10s (0:06:19)
```

无回归。A5 子项 8 测试全过；Phase 1 (6) + Phase 2 (101) 合计 107 测试全绿。

```text
$ ruff check tests/test_waker_phase2_e2e_a5.py
All checks passed!
```

## 与 Plan 的偏差

| Plan 写 | 实际 | 原因 |
|---------|------|------|
| 服务端层 100 + 50 断言 | 已在 `test_waker_phase2_acceptance.py:test_a5_replay_rejection_holds_across_sources` + `test_a4_replay_replay_path_only_one_row` 实现（7 个用例覆盖） | I2+I3 wake 时已落地（见 I2+I3 log §测试 - 服务端集成） |
| A5「怎么测」100/50 端到端 | 加 e2e_a5.py 验证 client pipeline + server 409 协作 | 端到端层验证 reviewer 红线「服务端 UNIQUE 不受豁免影响」；acceptance 测试只验 server 侧 |
| 「客户端二闸」= TTL + D4 | 已有（A4 wake 落地）；本测试聚焦其与 server 协作 | A4 已加 `_event_in_cooldown` 到 `_wake_event` 入口 |

无未达成 plan 项。

## 已知边界 / 给 reviewer 的备注

1. **服务端层断言已在 acceptance 文件覆盖**：A5 红线「服务端层重放 100 次同 fingerprint → 1 + 99」+「补漏路径 50 次同 fingerprint → 1 + 49」由 `tests/test_waker_phase2_acceptance.py` 7 个测试覆盖。本文件 (e2e_a5.py) 验证 client pipeline 行为，**两条断言已合并**。
2. **`server_first_sighting=False` 不模拟真实场景**：生产中 server 永远首见返回 True（同 agent 同 fingerprint 是 server UNIQUE 检查 409）。本测试用 `False` 模拟「server 已声明此 fingerprint」（如跨进程首次重投），验证 reviewer 关键 invariant。
3. **Test 5（cross-source）的 stats 计数**：replay TTL skip 计入 `stats.wake_skips`，polling skip 计入 `polling_stats.wake_skips`（两者独立 stats 对象）。两条断言确保各路径 skip 都被计数。
4. **与 heartbeat re-eval 路径的关系**：`had_prior_claim=True`（status=woken/server_skip）路径允许同 fingerprint 重 wake_async（plan §D3 「heartbeat re-eval after TTL → resume anyway」）。本测试不覆盖该路径（A5 红线聚焦幂等，不聚焦 retry）；A6/A7 在 acceptance 测试覆盖 re-eval 时的 server UNIQUE 行为。
5. **没有 `--force` 路径测试**：plan §A5 没有明确「client bypass + server UNIQUE」场景；本测试聚焦正常路径下的 server 409 处理。`--force` 行为属 v0.8 backlog（已在 cli/runtime_waker.py:1140 文档）。

## 下一步（I5-A6 → A7 → I6）

| 任务 | 何时 | 谁 |
|------|------|---|
| I5-A6：SSE 路径 join 测试（Phase 1 I3 三段 join + SSE 路径） | 下次 wake | host |
| I5-A7：sessions jsonl `event_source` 字段值集合 ⊆ {polling, sse, replay} 且 ≥ 2 种 | A6 后 | host |
| I6：与 Phase 1 P95 baseline informational 对照 + reviewer 提请评审 | I5 全完成后 | host |

## 执行结果

- 1 个新文件 `tests/test_waker_phase2_e2e_a5.py`，+520 行
- 8 个 A5 测试全过；Phase 1 + Phase 2 共 107 unique 测试无回归
- ruff check 通过

**A5 子项完成，等待 I5-A6。**

## 备注

- 本次 wake 不 acquire execution lock 的强烈需要（per skill: phase=running 不必强 lock；commit 是单文件原子操作，git 自然防并发）。本次锁是 `map --persona host experiment lock acquire` 主动申请（避免后续 I6 commit 时与潜在并行 wake 冲突），结束后通过 `lock release` 释放。
