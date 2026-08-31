---
title: "per-实验 token 成本台账（T5-B）：session jsonl 采集考证 + persona×实验聚合 + 只读报表"
acceptance:
  - "A1 采集分层：Layer 1 采集器（RawUsageEvent{event_type, ts, raw_payload} 不假设字段名）+ Layer 2 映射器（按 runtime 版本 `version+field_name` dict 映射成 NormalizedUsage）；缺字段显式 `unknown: {raw_field_name}` 元组不静默丢弃"
  - "A2 assistant + result 双采集：两处都采（result 在网络断开/超时场景可能丢失，assistant 留存）；assistant 累加 ≈ result，差 < 1% 进 sanity check WARN；差距 > 1% 触发 `pricing_unavailable` 告警"
  - "A3 归属映射三重 fallback：inline_field → file_window → post_accept_continuation → unmatched；同一 session 跨多实验按 first_experiment_id 全归首个；`matched_via` 推理路径写入聚合输出"
  - "A4 命令入口分工：`map experiment show --cost <id>` 单实验 × persona × session_kind 二维；`map waker costs --by-persona / --by-experiment` 跨实验汇总；复用同一 `lib/cost_ledger/` 库，jsonl 扫描结果缓存复用避免重复 IO"
  - "A5 `match_breakdown: {inline_field: N, file_window: N, post_accept_continuation: N, unmatched: N}` 默认输出（不需要 `--verbose` flag，监督者一眼能定位误归）"
  - "A6 cache 字段分桶：`cache_creation_input_tokens / cache_read_input_tokens` 独立计数，不并入 input；价目表缺价目只报 token + `pricing_unavailable` 标记，**不允许** 0 兜底"
  - "A7 损坏行告警不崩：warn 行但聚合继续；聚合无 cache（每次命令实时扫描 M 级文件 < 1s）；跨主机不做（明确单主机战役）"
  - "A8 回归测试 ≥8 case（tests/test_cost_ledger.py 新建）：多 persona 跨实验归属歧义 / 缺 usage 显式 unknown / 损坏行告警 / cache 分桶 / 命令分工 / 缺价目只报 token / match_breakdown 默认输出 / 端到端 spike jsonl 聚合对照 raw"
  - "A9 ruff check 0；pytest 全量绿（基线 1818 passed / 2 skipped / 359 deselected，只增不减，0 failed）；git diff 白名单 `^cli/`、`^sdk/`（如需类型）、`^tests/`"
  - "A10 边界：不动 waker 状态机/心跳/签名去重/热自检任何语义；不接外部计费 API；不回填历史 session 外数据；claude-runtime session jsonl append-only 读取不持文件锁"
evidence_keys:
  - "spike_note.md:3 实验 session jsonl 格式考证 + 字段映射表 + 损坏行样本（T5-A 715202a3 + T2 b3ec2e4d + T3 7aeabc2e 三类生命周期）；**修订记录**：原 plan 假设数据源 .map/runtime-waker-sessions/*.jsonl 不含 usage 字段，已纠正为 .map/claude-runtime-home-{host,participant,reviewer}/.claude/projects/-home-AI02-Documents-quantaeye-multi-agents-platform/*.jsonl"
  - "实测输出:(1) `map experiment show --cost <expB-id>` 单实验输出 match_breakdown + persona 二维；(2) `map waker costs --by-persona` 跨实验汇总输出；(3) mock 损坏行 → warn 不崩；(4) mock 缺价目 → `pricing_unavailable` 标记；(5) spike 三实验 raw jsonl 与聚合输出对账（误差 < 1%）"
  - "grep 核证:cli/cost_ledger/ 含 layer1_collector.py + layer2_mapper.py + attribution.py + render.py；lib/cost_ledger/ 含共享聚合逻辑；tests/test_cost_ledger.py 含 8 case"
  - "pytest_summary:≥8 case 全过（tests/test_cost_ledger.py），全量 pytest -q 0 failed"
dependencies:
  - "话题 experiment-cost-ledger（20d52df4）Round 1+2+3 共识收口（已 ready）"
  - "**真实数据源**（I0 spike 已证）：`.map/claude-runtime-home-{host,participant,reviewer}/.claude/projects/-home-AI02-Documents-quantaeye-multi-agents-platform/<session_id>.jsonl`；type=assistant 行的 message.usage.{input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens} 字段（Claude Agent SDK 输出）"
  - "原 plan 假设 `.map/runtime-waker-sessions/*.jsonl` 不含 usage 字段（仅 waker 事件日志），已被 I0 spike 证伪；详见 spike_note.md"
  - "战役 T5-A（715202a3）闭环：`map waker status` 实时态视图（实时态）+ 本实验历史态台账（历史态）= 监督 ROI 完整闭环"
  - "T1 8b1d20a1 签名去重 / T2 b3ec2e4d busy 拆分 / T3 7aeabc2e topic lifecycle / T4 d0c9dc5f drift 热自检 已落地链路无冲突"
  - "验收通过后由监督者重启 waker 生效（无 docker 镜像 build，仅 daemon restart）"
---

# per-实验 token 成本台账（T5-B）

## 背景

本战役 T5-A（715202a3）已落地 `map waker status` 实时态视图（waker 进程活性 + cycle + 错误），但 **per-实验 token 成本无单一入口**。监督者想看"这个实验花了多少 token / 哪个 persona 烧最多"必须逐 session grep jsonl，巡检耗时难以压缩。

T5-B 补完历史态：session jsonl usage 采集考证 + persona × 实验聚合 + 只读报表。T5-A + T5-B 叠加后监督者从「事后查」升级到「实时看 + 复盘算」。

## 任务

新增 `map experiment show --cost <id>` 单实验视图 + `map waker costs --by-persona / --by-experiment` 跨实验汇总：

- session jsonl 采集考证（spike 选 3 实验）+ Layer 1 采集器 + Layer 2 映射器
- 归属映射三重 fallback（inline_field → file_window → post_accept_continuation → unmatched）
- assistant + result 双采集 + sanity check 阈值 1%
- match_breakdown 默认输出（监督者一眼定位误归）

## 实施步骤

### I0 spike 选样 + 格式考证（前置）

- 选样规则：覆盖「短 / 中 / 长」3 类实验生命周期
  - T5-A 715202a3（完整闭环）
  - T2 b3ec2e4d（短链路）
  - T3 7aeabc2e（中等链路）
- 考证项：usage 字段命名（`input_tokens` vs `prompt_tokens` vs 其他）、assistant/result 出现模式、缺字段模式、损坏行模式
- 考证产物：`map/experiments/experiment-cost-ledger/spike_note.md`（写进 experiment dir），作为 I1 采集器编码依据
- 目的：避免 spike 阶段挑样偏差导致采集器对真实数据 schema 漏判

### I1 Layer 1 采集器（cli/cost_ledger/layer1_collector.py）

- 数据源：`.map/runtime-waker-sessions/*.jsonl`（append-only 读取，不阻塞 waker 写入）
- 输出：`RawUsageEvent{event_type, ts, raw_payload}` 列表
- 不假设字段名（采集器与映射器解耦）
- 损坏行处理：warn 行但聚合继续，不崩

### I2 Layer 2 映射器（cli/cost_ledger/layer2_mapper.py）

- 输入：RawUsageEvent 列表
- 输出：`NormalizedUsage{event_type, ts, input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, version, raw_field_name}`
- 映射表：`version+field_name` dict（如 `{"v1": {"prompt_tokens": "input_tokens"}, "v2": {"input_tokens": "input_tokens"}}`）
- 缺字段处理：显式 `unknown: {raw_field_name}` 元组，不静默丢弃
- cache 字段分桶：`cache_creation_input_tokens / cache_read_input_tokens` 独立计数，不并入 input

### I3 归属映射（cli/cost_ledger/attribution.py）

- 三重 fallback（按优先级）：
  1. `inline_field`：session jsonl 含显式 `experiment_id` 字段（最优先）
  2. `file_window`：session 文件名/路径含 experiment_id（如 `exp_<uuid>.jsonl`）
  3. `post_accept_continuation`：session 时间窗口与 experiment start_at 重叠
  4. `unmatched`：归入 unknown bucket（不丢）
- 同一 session 跨多实验：按 first_experiment_id 全归首个（避免重复计数）
- `matched_via` 推理路径写入聚合输出

### I4 命令入口（cli/cost_ledger/render.py + cli/commands/）

- 单实验：`map experiment show --cost <id>` → persona × session_kind 二维
- 跨实验：`map waker costs --by-persona` / `map waker costs --by-experiment`
- 复用同一 `lib/cost_ledger/` 库，jsonl 扫描结果缓存复用避免重复 IO
- 聚合无 cache（每次命令实时扫描 M 级文件 < 1s）

### I5 match_breakdown 默认输出（与 A5 一致）

```yaml
experiment_id: <uuid>
persona_breakdown:
  host: {tokens: ..., sessions: ...}
  participant: {tokens: ..., sessions: ...}
match_breakdown:
  inline_field: 42
  file_window: 18
  post_accept_continuation: 7
  unmatched: 3
pricing_source_date: '2026-08-31'
pricing_unavailable: false
```

不需要 `--verbose` flag，监督者一眼能定位误归。

### I6 sanity check（与 A2 一致）

- assistant 累加 ≈ result（差 < 1%）
- 差距 > 1% WARN（不阻塞输出，但标记）
- 缺价目：`pricing_unavailable: true` + 只报 token（不允许 0 兜底）

### I7 回归测试（tests/test_cost_ledger.py 新建 ≥8 case）

- (a) 多 persona 跨实验归属歧义：first_experiment_id 全归首个 + `matched_via` 记录
- (b) 缺 usage 显式 unknown：`unknown_count + unknown_pct + 最近一条 example session_id`（不带内容）
- (c) 损坏行告警不崩：warn 行但聚合继续
- (d) cache 字段分桶：`cache_creation_input_tokens / cache_read_input_tokens` 独立计数
- (e) 命令入口分工：`map experiment show --cost` 单实验 vs `map waker costs --by-persona` 跨实验
- (f) 价目表缺价目只报 token：env 无 override + 内置无 model → 输出 token 数 + `pricing_unavailable` 标记
- (g) `match_breakdown` 默认输出（与 A5 对齐）
- (h) 端到端 spike jsonl 聚合对照 raw：spike 三实验 raw jsonl 与聚合输出对账（误差 < 1%）

### I8 commit + log + release

- 窄 commit 白名单 `^cli/`、`^sdk/`（如需类型）、`^tests/`
- ruff check 0 + pytest 全量绿
- complete log → reviewer → done

## 风险与边界

- 不动 waker 状态机/心跳/签名去重/热自检任何语义（与 T1-T4 已落地链路无冲突）
- 不接外部计费 API（价目表内置 + env override）
- 不回填历史 session 外数据（明确单主机战役）
- session jsonl append-only 读取不持文件锁（B5 护栏）
- 聚合无 cache（每次命令实时扫描，避免 cache 复杂度）
- assistant + result 双采集（B2 护栏，sanity check 阈值 1%）
- 不复用 T5-A state.json（不同数据源：state.json = waker 进程状态，session jsonl = token 用量）
- 与 T5-A 主题联动：实时态（T5-A）+ 历史态（本实验）= 监督 ROI 完整闭环

## Co-author

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
