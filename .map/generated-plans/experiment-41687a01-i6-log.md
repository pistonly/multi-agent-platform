# I6 执行日志：结果汇总 + Phase 1 baseline 对照 + 上线 checklist + reviewer 提请评审

> 实验：`41687a01-3992-471b-b415-8ad80f732f80` — waker Phase 2：SSE 叠加 + lifecycle 事件补 publish + 重连补偿
> 当前 plan version：2；I1–I5 全部 7 子项完成；本日志覆盖 **I6（最终结果整理 + 提请评审）**。

## 范围与目标

| 子项 | 来源 | 范围 |
|------|------|------|
| **Phase 2 baseline 落盘** | plan §I6 | 汇总 A1a/A1b/A1总/A2/A3/A4/A5/A6/A7 数字到 `.map/generated-plans/phase2-p95-baseline.json` |
| **Phase 1 vs Phase 2 informational 对照** | plan §I6 + §硬性约束 + U1 回应 | 不同口径并列说明，不做硬比对 |
| **A1a/A1b 拆分报告** | plan §I6 + U5 回应 | 三个独立数字（传输 / waker overhead / CLI startup），瓶颈归属清晰 |
| **上线 checklist 落地** | plan §I6 | `docs/MAP-RUNTIME-WAKER.md` 新增「Phase 2 SSE overlay rollout checklist (v0.8)」章节 |
| **Reviewer 提请评审** | plan §I6 | `map experiment complete` 提交 `result_review` |

## 改动清单

| 文件 | 变更 |
|------|------|
| `.map/generated-plans/phase2-p95-baseline.json` (NEW) | 汇总 9 段 baseline：scope 差异、A1a/A1b/A1总/A2/A3/A4/A5/A6/A7 + informational 与 Phase 1 对照 |
| `docs/MAP-RUNTIME-WAKER.md` | 末尾新增「Phase 2 SSE overlay rollout checklist (v0.8)」一节（62 行），含 5 张表（验收摘要 / 上线 checklist / 关键数字 / 已知边界 / Reviewer 提请项）|

合计：1 文件新增，1 文件 +62 / -0 行。

## 关键数字（reviewer 提请评审必看）

### 端到端 P95 by kind vs reviewer 红线

| kind | reviewer 红线 | 实测 | 余量 |
|------|---------------|------|------|
| `mention` | < 5s | 2105.2ms | 58% |
| `pending_review` | < 10s | 2105.0ms | 79% |
| `topic_lifecycle` | < 30s | 2105.1ms | 93% |

三 kind P95 几乎相同（2105±0.2ms），因为 `_wake_event` 路由不依赖 fingerprint 内容；红线差距反映运营紧急度，不反映代码路径差异。瓶颈是 Claude Code CLI 启动（~2.1s），warm-pool 优化归 v0.8。

### A1a/A1b 拆分（A1 拆分纪律，回应 U5）

| 组件 | 实测 | 含义 |
|------|------|------|
| A1a SSE 帧传输 | mean 0.5ms / p95 0.5ms / max 0.5ms | 5 trials 跨 persona 真 docker API |
| A1b waker overhead | 2.1ms | `_wake_event` → `wake_async` 入 → 出（500ms stub 模拟下） |
| A1b Claude CLI 启动 | 冷 2087ms / 暖中位 2072ms | 真 `claude --print` 3 trials |
| **A1总 估算** | ≈ 2102ms | 三段 composition 总和 |
| **A1总 实测 P95** | 2105ms | 20 trials 走真 `_wake_event` 路径 |

A1a 硬门槛 < 1s 远低于实测（2000× 余量），CLI 启动瓶颈在 v0.8 warm-pool 优化范围内，不在 Phase 2 红线内。

### A2 / A3 / A4 / A5 全部满足 reviewer 红线

| 项 | 红线 | 实测 |
|----|------|------|
| A2 漏事件率 | = 0 | 3/3 + 10/10 + 30/30 三档全部补漏；source 区分 replay/sse/polling |
| A3 空轮询比例稳态期 | ≥ 95% | 档 b 100% / 档 a 90%（受控流量段不直接套红线）；recovery 期不计分母 |
| A4 重复唤醒率 | < 0.1% | 5 场景全 ≤ 1 wake（含 SSE storm 1000 + replay flood 50 + mixed paths） |
| A5 幂等写成功率 | 100% | 7 场景全 ≤ 1 wake；server UNIQUE 拒绝首呼时 0 wake（D4 豁免不影响 server UNIQUE） |
| A6 审计三段 join | 等值 join | SSE 路径 3-way (notification.id == inbound_event.event_id == sessions_jsonl.event_id) + Phase 1 5 case 无回归 |
| A7 sessions jsonl event_source | ⊆ {polling, sse, replay} 且 ≥ 2 种 | 三值全部覆盖 + replay 必填 |

## Phase 1 vs Phase 2 informational 对照

| 维度 | Phase 1 baseline | Phase 2 baseline | 口径差异 |
|------|-----------------|------------------|---------|
| 测量对象 | `inbound_event` UNIQUE gate DB write 时间 | 端到端 notification → wake_resume | Phase 1 不含 SSE / wake；Phase 2 不含 jsonl append-only 内部开销 |
| mentions P95 | 0.000854s | 2.105s | Phase 2 ≈ Phase 1 + SSE 传输 + waker 路径 + CLI 启动 |
| 红线 | 无 end-to-end 红线（Phase 1 baseline 自带 `note: "informational, no threshold"`） | reviewer 红线 mention < 5s / pending_review < 10s / topic_lifecycle < 30s | Phase 1 无可比对的红线 |
| 状态 | 落盘 `.map/generated-plans/phase1-p95-baseline.json` | 落盘 `.map/generated-plans/phase2-p95-baseline.json` | 两文件并存，分别 informational 对照 |

Phase 1 + Phase 2 baseline JSON 结构相似（同 `phase` / `captured_at` / `note` / 各 kind P95 字段），便于 reviewer 用同一脚本对照。

## 上线 checklist 摘要（详见 `docs/MAP-RUNTIME-WAKER.md`）

生产 / 准生产启用 SSE 主路径前 9 步验证：
1. 拉取 Phase 2 commits（HEAD 含 `580713c` 等）
2. 跑 111 个 Phase 1+2 测试 → 全绿
3. 跑 `ruff check` → clean
4. dry-run 启动 waker → 三 persona 启动 + polling 兜底在
5. 注入合成 notification 验证 SSE 主路径 → 5s 内收到 wake
6. kill API → 30s 内恢复 → sessions jsonl 出现 `event_source="replay"`
7. `MAP_RUNTIME_SSE_ENABLED=0` → polling 主路径仍工作（escape hatch）
8. 监控基线 → 24h 无异常堆积
9. 回滚预案 → `MAP_RUNTIME_SSE_ENABLED=0` 立即生效

## Reviewer 提请项（result_review 阶段需 reviewer 确认）

1. **A1 红线出处可追溯**（U1 回应）：plan v2 §A1总 + §硬性约束显式声明红线 = reviewer 立场 `bf3f263d` + Round 1 Summary `3f9d80cd` 共识 7。
2. **D4 补漏豁免无滥用**（U2 回应）：仅 `event_source="replay"` 路径豁免；服务端 UNIQUE 主闸不受影响（A5 测试 3/4 显式验证）。
3. **A3 双档 + 边界分段**（U3 + U4 回应）：档 b 100% / 档 a 90%（受控段不直接套 95%）；recovery 期不计分母；启动 / 稳态 / 恢复 / post-recovery 四段报表完整。
4. **A1 拆分 A1a + A1b**（U5 回应）：A1a 传输硬门槛 < 1s 通过；A1b CLI 启动 ~2.1s 观测项无门槛（warm-pool 优化归 v0.8）。
5. **B 实验未 commit 代码的根因**（reviewer process bug）：见 I2+I3 source commit log §风险与告知；本次实验 commit 节奏与 B 不同，但 result_review 时需 reviewer 知悉。
6. **Phase 1 ↔ Phase 2 baseline 口径差异**：不同 scope，仅 informational 对照；不试图将 Phase 1 的 0.85ms 与 Phase 2 的 2.1s 直接相比。

## 验证

```text
$ pytest tests/test_waker_phase1_acceptance.py tests/test_waker_phase2_i1.py \
         tests/test_waker_phase2_i2_i3.py tests/test_waker_phase2_acceptance.py \
         tests/test_waker_phase2_sse_consumer.py tests/test_waker_phase2_e2e_a1a.py \
         tests/test_waker_phase2_e2e_a1b.py tests/test_waker_phase2_e2e_a1_total.py \
         tests/test_waker_phase2_e2e_a2.py tests/test_waker_phase2_e2e_a3.py \
         tests/test_waker_phase2_e2e_a4.py tests/test_waker_phase2_e2e_a5.py \
         tests/test_waker_phase2_e2e_a6_a7.py -q --no-header
........................................................................ [ 64%]
.......................................                                  [100%]
111 passed in 326.54s (0:05:26)

$ ruff check docs/MAP-RUNTIME-WAKER.md
All checks passed!

$ python -c "import json; d=json.load(open('.map/generated-plans/phase2-p95-baseline.json')); print('OK,', len(d), 'top keys')"
OK, 18 top keys
```

## 与 Plan 的偏差

| Plan 写 | 实际 | 原因 |
|---------|------|------|
| 「产出 `.map/generated-plans/phase2-p95-baseline.json`」 | 同名文件落盘 | 与 Phase 1 baseline 对称命名 |
| 「更新 `docs/MAP-RUNTIME-WAKER.md` 上线 checklist」 | 末尾新增 62 行 rollout 章节 | 保留原文档结构（`## Session wake logs` / `## Boundary` / `## v0.8 保留正则` / `## 生产 systemd 部署`），新增章节与原风格一致 |
| 「与 Phase 1 P95 baseline informational 对照」 | baseline JSON 内 `informational_comparison_phase1` 字段并列 | 不同口径，明确不做硬比对 |
| 「A1a/A1b 拆分报告」 | baseline JSON + 文档双处体现 | JSON 字段数值精准，文档表格带 reviewer 友好摘要 |
| 「reviewer 提请评审」 | 6 项显式列出 + `map experiment complete` 提交 | 让 reviewer 在 result_review 阶段有明确的核对清单 |

无未达成 plan 项。

## 执行结果

- 1 个新文件 `.map/generated-plans/phase2-p95-baseline.json`
- 1 个修改文件 `docs/MAP-RUNTIME-WAKER.md` +62 / -0
- 111 测试全绿；ruff clean；JSON 解析 OK

**I6 子项完成。下一步：调用 `map experiment complete` 提交 result_review。**

## 备注

- 本次 wake 已 acquire execution lock（之前 `map experiment lock acquire --id 41687a01-...`），在 complete 后 release。
- 完成 complete 后，本实验状态变为 `result_review`，等 reviewer `accept-result` 或 `reject-result`。
- 提请评审项 1–5 均已在 plan v2 + I2+I3 log + I4 log 中分散说明；I6 把它们收口到一处，方便 reviewer 一次性核对。
