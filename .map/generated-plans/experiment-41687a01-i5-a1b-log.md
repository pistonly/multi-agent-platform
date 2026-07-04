# I5-A1b 执行日志：Claude Code CLI 后端 wake 延迟观测基线

> 实验：`41687a01-3992-471b-b415-8ad80f732f80` — waker Phase 2：SSE 叠加 + lifecycle 事件补 publish + 重连补偿
> 当前 plan version：2；I1-I5-A1a 已完成；本日志覆盖 **I5-A1b**（观测基线，无门槛）。

## 范围与目标

Plan §A1b — observation baseline，**无准入门槛**（warm-pool 优化归 v0.8 backlog）。
测量 `A1b = t_wake_async_return − t_wake_event_entry`，拆解为：

- **waker overhead**：`_wake_event` → `backend.wake_async` 入口之间
- **Claude CLI 启动耗时**：subprocess spawn + SDK connect + query + receive_response

plan 估的 3–8s 实测区间集中在 Claude CLI 启动；waker 代码路径应该是 sub-100ms 量级。
本次目的是**首次落盘 baseline 数字**，方便 v0.8 warm-pool 优化有目标值可对照。

## 改动清单

| 文件 | 变更 |
|------|------|
| `tests/test_waker_phase2_e2e_a1b.py` | 新增 3 个测试：A1b baseline 报告（实 CLI + stub + JSON） |

合计：1 文件新增，+228 行。

## 设计要点

### Test 1: Real Claude CLI subprocess startup（实测）

直接 spawn `claude --print --dangerously-skip-permissions "<ping>"`，3 trials 取冷/暖中位：

- `--print` 模式跳过交互，直接 stdout 单次响应 → 测量的是「CLI 启动 + 单轮 turn」的纯净下限
- `--dangerously-skip-permissions` 跳过权限弹窗，避免污染时延
- 3 trials 是为了让 OS page cache 暖一次再取中位

### Test 2: Waker path overhead with stub backend（受控）

`_Stub(PersonaAgentWakeBackend)` 子类，覆写 `wake_async` 用 `asyncio.sleep(SIMULATED_CLI_SECONDS)`
模拟 CLI 启动；走真 `_wake_event` 路径但绕开 Claude SDK。

测量：

- `A1b = t_wake_async_return − t_entry`（wall-clock）
- `overhead = A1b − SIMULATED_CLI_SECONDS × 1000`（剥离模拟延迟）
- `overhead < OVERHEAD_BUDGET_MS`（500ms 宽限；真路径在 ~2ms 量级）

stub 继承 `PersonaAgentWakeBackend` 是因为 `_wake_event` 走 `isinstance(self.backend, PersonaAgentWakeBackend)`
分支调 `await self.backend.wake_async(...)`；非该类会落 `else` 分支调同步 `self.backend.wake(...)`，
无法测异步路径。

`_StubMapClient` 内联继承 `MapCommandClient`，覆写 `whoami` / `todos` / `notifications_unread` /
`inbound_event_record`；record 永远返回 True（首次见 fingerprint），跳过服务端 UNIQUE 主闸。

### Test 3: Baseline JSON emitter

合并 test 1 + stub 信息写到 `tmp_path/phase2-a1b-baseline.json`，
canonical 路径 `.map/generated-plans/phase2-p95-baseline.json` 留到 I6 收口时与 A1a 汇总。

## 测试结果

```text
$ pytest tests/test_waker_phase2_e2e_a1b.py -v -s

tests/test_waker_phase2_e2e_a1b.py::test_real_claude_cli_subprocess_startup
[a1b] Claude CLI cold=2087ms warm_median=2072ms min=2046ms max=2072ms
       trials=3 stdout_samples=['ok']
PASSED

tests/test_waker_phase2_e2e_a1b.py::test_wake_async_path_overhead_with_stub_backend
[a1b] waker_overhead A1b=502.1ms cli_simulated=500ms overhead=2.1ms
PASSED

tests/test_waker_phase2_e2e_a1b.py::test_a1b_baseline_emitted
[a1b] baseline emitted to /tmp/pytest-of-AI02/pytest-434/.../phase2-a1b-baseline.json
PASSED

============================== 3 passed in 8.39s ==============================
```

**Baseline 数字**：

| 维度 | 实测 | 含义 |
|------|------|------|
| Claude CLI 冷启动 | 2087 ms | 单次 `claude --print` 从 spawn 到 stdout 收尾 |
| Claude CLI 暖中位 | 2072 ms | 三 trials 中第 2 次起的 median（OS page cache 暖） |
| Claude CLI 暖 min | 2046 ms | 三 trials 中第 2 次起的 min |
| Waker overhead | 2.1 ms | `_wake_event` → `wake_async` 入 → 出的纯路径开销（500ms stub 模拟下） |
| 合计 A1b 估算 | ~2.1 s | 实 CLI 启动 + 2ms waker 开销 ≈ 2.1s 端到端 |

### 与 Plan 估算的对比

plan 估的 ~3–8s 偏保守，本机实测冷/暖都在 **~2s** —— 比预估快 30–75%。原因可能是：

- `--print` 模式比完整 agent mode 轻（无 TUI、无 MCP server 加载）
- 本机 Claude SDK 版本（2.1.198）相对 plan 起草时可能已有优化
- 真实端到端（含 Claude Code 实际 LLM 调用）会显著高于此 baseline（几百 ms ~ 数秒 LLM 推理）

### 含义与后续

- A1b 的「实测 ~3–8s」基线被本机数据 refine 到 **~2s**。v0.8 warm-pool 优化的目标值应该是：
  - 目标：把「已经 warm 过的 Claude session 复用」使 A1b 降到 500ms 量级
  - 上限：避免把 A1b 推到 >5s（reviewer A1 总红线 mention < 5s）
- Waker 代码路径 overhead（2.1ms）已远低于 CLI 启动（2s），无需在 `_wake_event` 做性能优化
- A1b 没有 reviewer 硬门槛，仅观测；本次落盘的 baseline 让 v0.8 backlog 有量化目标

## 与 Plan 的偏差

| Plan 写 | 实际 | 原因 |
|---------|------|------|
| 「注入合成 notification → waker `_wake_event` resume 返回 → 记录 A1b」 | Test 1 用真 CLI subprocess + Test 2 用 stub 走真 `_wake_event` 路径 | 实注入合成 notification 经 SSE 触发需要 waker 进程在跑（与 A1a e2e 模型重叠）；本次拆为「CLI 启动下限（test 1）」+「waker 路径 overhead（test 2）」两段，独立测量、便于 reviewer 横向比较 |
| 写到 `.map/generated-plans/phase2-p95-baseline.json` | 写到 `tmp_path/phase2-a1b-baseline.json` | 与 A1a 一致：canonical baseline 等 I6 收口时与 A1a + A2 + A3 一起汇总，避免中途反复改 canonical 文件 |
| `--once` 跑真 waker 一周期再读 sessions jsonl | 改用真 CLI subprocess（test 1）+ stub 走 `_wake_event`（test 2） | `--once` 跑一次会 cancel in-flight SSE task；sessions jsonl 等待需要更长 timeout。本次设计更快、更可重复 |

无未达成 plan 项。

## 下一步（I5-A1总 → I5-A2 → I5-A3）

| 任务 | 何时 | 谁 |
|------|------|---|
| I5-A1总：端到端 P95 by kind 注入测 + 与 reviewer 红线硬比对 | 下次 wake | host |
| I5-A2：SSE 断线 30s / 5min / 30min 三档补漏 + `event_source="replay"` 验证 | A1总 后 | host |
| I5-A3：双档受控流量（N=10 + 空载基线）+ 3 段报表 | A2 后 | host |
| I6：与 Phase 1 P95 baseline informational 对照 + reviewer 提请评审 | I5 全完成后 | host |

## 执行结果

```text
$ pytest tests/test_waker_phase2_e2e_a1b.py -v
3 passed in 8.39s

$ pytest tests/test_waker_phase2_e2e_a1b.py tests/test_waker_phase2_e2e_a1a.py \
        tests/test_waker_phase2_i2_i3.py tests/test_waker_phase2_acceptance.py \
        tests/test_waker_phase2_sse_consumer.py tests/test_waker_phase2_i1.py \
        tests/test_waker_phase1_acceptance.py -q --no-header
76 passed in 155.52s (0:02:35)

$ ruff check tests/test_waker_phase2_e2e_a1b.py
All checks passed!
```

新增 1 文件：`tests/test_waker_phase2_e2e_a1b.py`，228 行。Phase 2 测试套件从 70 → 76（+3 A1b + A1a 3），
Phase 1 6 个回归测试无破坏。
