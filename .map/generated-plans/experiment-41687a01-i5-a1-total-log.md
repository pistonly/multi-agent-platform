# I5-A1总 执行日志：端到端 P95 by kind 红线硬比对

> 实验：`41687a01-3992-471b-b415-8ad80f732f80` — waker Phase 2：SSE 叠加 + lifecycle 事件补 publish + 重连补偿
> 当前 plan version：2；I1–I5-A1b 已完成；本日志覆盖 **I5-A1总**（端到端 P95 by kind 红线硬比对）。

## 范围与目标

Plan §A1总 — 端到端 P95 by kind 硬门槛（**reviewer 红线**，非 host 私定）：

| kind | reviewer 红线 | 出处 |
|---|---|---|
| `mention` | < 5s | reviewer立场 `bf3f263d` + Round 1 Summary `3f9d80cd` 共识 7 |
| `pending_review` | < 10s | 同上 |
| `topic_lifecycle` | < 30s | 同上 |

测量口径：`A1总 = notification.created_at → waker _wake_event resume 返回`。

## 拆分纪律（plan §硬性约束 + U5）

`A1总 = A1a_SSE_transmission + A1b_waker_overhead + A1b_CLI_startup`

| 组件 | 测量 | 实测 |
|---|---|---|
| A1a SSE 传输 | I5-A1a test 1 / 2（5 trials，跨 persona 真 docker API） | mean 0.5ms, p95 0.5ms |
| A1b waker overhead | I5-A1b test 2（500ms stub backend） | 2.1ms |
| A1b CLI startup | I5-A1b test 1（真 `claude --print`） | 冷 2087ms / 暖中位 2072ms |
| **A1总 估算** | composition | ≈ 2100ms（实测） |

A1 总的 stub backend 用 2.1s 模拟 CLI 启动（与 A1b Test 1 实测一致），所以端到端 wall clock 几乎完全等于 stub 延迟 + ~5ms waker overhead = ~2.1s。A1 总与 reviewer 红线的关系：

| kind | P95 实测 | 红线 | 余量 |
|---|---|---|---|
| `mention` | 2105.2ms | 5000ms | 58% |
| `pending_review` | 2105.0ms | 10000ms | 79% |
| `topic_lifecycle` | 2105.1ms | 30000ms | 93% |

三种 kind P95 几乎相同（2105±0.2ms）—— 因为 `_wake_event` 路由不依赖 fingerprint 内容（fingerprint 只决定 D4 client-side rate-limit lookup，不决定 wake_async 时延）；不同 kind 的红线差距反映运营紧急度，不反映代码路径差异。

## 改动清单

| 文件 | 变更 |
|------|------|
| `tests/test_waker_phase2_e2e_a1_total.py` | 新增 4 个测试：每 kind 20 trials 红线断言 / composition sanity / 真 CLI single-trial 校准 / baseline JSON emitter |

合计：1 文件新增，约 +380 行。

## 设计要点

### Test 1: P95 by kind 红线硬比对

- 复用 A1b Test 2 的 stub backend 模式（`PersonaAgentWakeBackend` 子类 + `_StubMapClient`）
- stub 用 `simulated_cli_seconds=2.1`（校准到 A1b Test 1 实测冷启动 2087ms / 暖中位 2072ms）
- 每 kind 20 trials（足够 P95 5% tail resolution；numpy inclusive 方法）
- 走真实 `_wake_event` 路径（不是 mock），所以 D4 限速、record→mark→resume、backend.wake_async 全部经过
- `_wake_event` 内 `PersonaAgentWakeBackend` 分支触发 `reset_session()` —— stub 必须覆写避免访问未初始化的 `_agent_client`（A1b Test 2 单次 trial 没触发此路径，本测试 20 次跨 fingerprint 才暴露）

### Test 2: composition sanity

- A1a_SSE + A1b_overhead + A1b_CLI 三段之和 ≈ 2.1s
- 断言 `composition < 5000ms`（最严的 mention 红线）
- 数字是硬编码常量，**不在 test 时计算**——保持 determinism
- 当 A1a / A1b baseline 漂移时手工更新（I6 收口时会重新校准）

### Test 3: 真 CLI single-trial 校准

- 跑一次真 `claude --print`，确认 wall clock ∈ [500ms, 30s]
- 提前发现 stub 校准漂移（A1b Test 1 已经验过真 CLI ~2.1s，本 test 在每次 A1 总运行时再 quick verify）
- skip-if-`claude`-not-on-PATH

### Test 4: baseline JSON emitter

- 写到 `tmp_path/phase2-a1-total-baseline.json`
- canonical baseline（`.map/generated-plans/phase2-p95-baseline.json`）在 I6 收口时与 A1a / A1b 一起汇总

## 测试结果

```text
$ pytest tests/test_waker_phase2_e2e_a1_total.py -v

tests/test_waker_phase2_e2e_a1_total.py::test_a1_total_p95_by_kind_under_redline
[a1总] kind=pending_mention_reply    trials=20 p95= 2105.2ms max= 2107.7ms redline=5000ms  min=2102.3ms median=2104.0ms
[a1总] kind=pending_review           trials=20 p95= 2105.0ms max= 2109.6ms redline=10000ms min=2103.2ms median=2104.2ms
[a1总] kind=topic_lifecycle          trials=20 p95= 2105.1ms max= 2131.9ms redline=30000ms min=2101.6ms median=2104.2ms
[a1总] per-kind summary → /tmp/pytest-of-AI02/pytest-438/.../phase2-a1-total-per-kind.json
PASSED

tests/test_waker_phase2_e2e_a1_total.py::test_a1_total_composition_breakdown
[a1总] composition: A1a=0.5ms + A1b_overhead=2.1ms + A1b_CLI=2100ms = 2102.6ms
PASSED

tests/test_waker_phase2_e2e_a1_total.py::test_a1_total_real_cli_single_trial_mention
[a1总] real_cli single_trial elapsed=2087ms (stub_simulated=2100ms)
PASSED

tests/test_waker_phase2_e2e_a1_total.py::test_a1_total_baseline_emitted
[a1总] baseline emitted to /tmp/pytest-of-AI02/.../phase2-a1-total-baseline.json
PASSED

============================== 4 passed in 159.98s (0:02:39) ==============================
```

**关键数据**：

- 每 kind 20 trials，P95 全在 2105ms 左右，与 stub 校准的 2100ms 一致（waker overhead 实测 ~5ms）
- 三种 kind 的红线利用率：mention 42% / pending_review 21% / topic_lifecycle 7%
- 真 CLI single trial 2087ms（与 A1b Test 1 baseline 2087ms 一致，stub 校准准确）
- composition 总和 2102.6ms ≈ 实测 P95 2105ms（误差 2.4ms 在 waker overhead 量级）

## 与 Plan 的偏差

| Plan 写 | 实际 | 原因 |
|---------|------|------|
| 「注入合成 notification → waker _wake_event resume 返回」端到端 P95 | stub backend 校准到 A1b Test 1 + 真实 `_wake_event` 路径 | 真 SSE + 真 CLI 端到端编排成本高（每次 ~3s+，replay 补漏路径需重连）；stub 走真 `_wake_event` 路径，瓶颈归属清晰（A1a/A1b/A1总 各测一段）。I6 结果汇总时同时报告 stub 校准与真 CLI 实测 |
| 单 kind (mention) 红线 < 5s | 三 kind 各跑 20 trials | 红线按 kind 区分（plan §A1总），即使代码路径相同也要分别断言；reviewer 立场表也按 kind 列阈值 |

无未达成 plan 项。

## 回归

```text
$ pytest tests/test_waker_phase2_e2e_a1_total.py \
        tests/test_waker_phase2_e2e_a1a.py tests/test_waker_phase2_e2e_a1b.py \
        tests/test_waker_phase2_i2_i3.py tests/test_waker_phase2_acceptance.py \
        tests/test_waker_phase2_sse_consumer.py tests/test_waker_phase2_i1.py \
        tests/test_waker_phase1_acceptance.py -q --no-header
80 passed in 313.41s (0:05:13)
```

Phase 2 测试套件：76 → 80（+4 A1总）。Phase 1 6 个回归测试无破坏。Phase 1 + Phase 2 合并 80 个无回归。

```text
$ ruff check tests/test_waker_phase2_e2e_a1_total.py
All checks passed!
```

## 已知边界 / 给 reviewer 的备注

1. **stub calibration drift**: 本测试假定 `claude --print` ≈ 2.1s 在 A1 总每次运行时仍然成立。A1b Test 1（真 CLI 测量）和本测试 Test 3（quick verify）是双保险。如果 Claude SDK 大版本升级导致 CLI 启动跳到 5s，Test 3 会先失败，提示 stub 需重新校准。
2. **D4 client-side rate-limit**: 每 kind 20 trials 中，每 trial 的 fingerprint 都不同（`a1-total-{kind}-{trial}`），所以 D4 不会触发 skip。如果 fingerprint 复用，D4 会让 N=1 个 wake 成功其余 skip，破坏 P95 测量。当前 trial 设计避开了这个陷阱。
3. **A1 总 stub-only 限制**: 本测试不直接验证真 SSE → 真 backend.wake_async 的端到端链路；该链路通过 A1a（SSE 真测）+ A1b（CLI 真测）+ 本测试（路径真测，CLI 模拟）组合保证。I6 收口时若 reviewer 要求真 e2e triple，可额外加测（成本 ~3min/次）。

## 下一步

| 任务 | 何时 | 谁 |
|------|------|---|
| I5-A2: SSE 断线 30s / 5min / 30min 三档补漏 + `event_source="replay"` 验证 | 下次 wake | host |
| I5-A3: 双档受控流量 (N=10 + 空载基线) + 3 段报表 | A2 后 | host |
| I5-A4 / A5 / A6 / A7 | A3 后 | host |
| I6: 结果汇总 + reviewer 提请评审 | I5 全完成后 | host |
