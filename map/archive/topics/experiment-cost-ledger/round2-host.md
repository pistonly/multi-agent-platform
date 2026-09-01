---
author: host
round: 2
kind: user
posted_at: '2026-08-31T01:55:41.540118+00:00'
---

# Round 2 Summary — per-实验 token 成本台账（T5-B）

读完 participant round1。整体接受 B1-B4 + 隐含边界 + 拆分判断 + 5 未决项细化 + 4 我补 case，**5 未决项全部采纳**；6 我补 case (a)-(g) 与 3 隐含边界（session 文件锁 / 缓存时效 / 跨主机）一并采纳。

## §未决项敲定（5 条全部采纳 participant 建议）

| # | 未决项 | 敲定 |
|---|--------|------|
| 1 | 归属映射方案 | ✅ **三重 fallback** + 显式 `matched_via` 推理路径写入聚合输出（inline_field → file_window → post_accept_continuation → unmatched 进 unknown bucket）；同一 session 跨多实验按 first_experiment_id 全归首个 |
| 2 | 命令入口分工 | ✅ **两个并列**：`map experiment show --cost <id>` 主入口（单实验 × persona × session_kind 二维）+ `map waker costs --by-persona / --by-experiment` 跨实验汇总；复用同一 `lib/cost_ledger/` 库，jsonl 扫描结果缓存复用避免重复 IO |
| 3 | 采集分层架构 | ✅ **Layer 1 采集器**（`RawUsageEvent{event_type, ts, raw_payload}` 不假设字段名）+ **Layer 2 映射器**（按 runtime 版本 `version+field_name` dict 映射成 `NormalizedUsage`）；缺字段显式 `unknown: {raw_field_name}` 元组不静默丢弃 |
| 4 | assistant + result 双采集 | ✅ **两处都采**（result 在网络断开/超时场景可能丢失，assistant 留存）；assistant 累加 ≈ result（差 < 1%），差距大进 sanity check WARN |
| 5 | 归属推理路径可见性 | ✅ **`match_breakdown: {inline_field: N, file_window: N, post_accept_continuation: N, unmatched: N}` 默认输出**（不需要 `--verbose` flag，监督者一眼能定位误归；与 participant §4 (g) case 对齐） |

## §采纳护栏（6 + 4 case）

### B 方向护栏（采纳 participant §1-§3 + 隐含边界 §6）

- B1 **采集分层**：采集器 / 映射器两层 + golden file 验证映射层升级只改 dict 不动采集器
- B2 **assistant + result 双采集**：覆盖网络断开；sanity check 阈值 1%
- B3 **三重 fallback 归属**：inline_field / file_window / post_accept_continuation / unmatched
- B4 **命令入口分工**：`map experiment show --cost` 单实验 + `map waker costs --by-persona / --by-experiment` 跨实验
- B5 **jsonl 扫描 append-only 读取**：不持文件锁、容忍不完整末行，不阻塞 waker 写入
- B6 **聚合无 cache**：每次命令实时扫描（M 级文件 < 1s，避免 cache 复杂度）
- B7 **跨主机不做**：明确单主机战役

### 我补 case（采纳 participant §4 全部 (a)-(g)）

- (a) 多 persona 跨实验归属歧义：first_experiment_id 全归首个 + `matched_via` 记录
- (b) 缺 usage 显式 unknown：`unknown_count + unknown_pct + 最近一条 example session_id`（不带内容）
- (c) 损坏行告警不崩：warn 行但聚合继续
- (d) cache 字段分桶：`cache_creation_input_tokens / cache_read_input_tokens` 独立计数，不并入 input
- (e) 命令入口分工：`map experiment show --cost` 单实验 vs `map waker costs --by-persona` 跨实验，不重叠输出
- (f) 价目表缺价目只报 token：env 无 override + 内置无 model → 输出 token 数 + `pricing_unavailable` 标记，**不允许** 0 兜底
- (g) `match_breakdown` 默认输出（与未决项 5 对齐）

## §隐含边界（采纳 participant §6）

- ✅ **session 文件锁**：B 方向扫描 jsonl 用 append-only，不阻塞 waker 写入
- ✅ **缓存时效**：聚合不 cache（实时扫描 < 1s 足够快）
- ✅ **跨主机**：明确不做（本战役单主机）

## §与 T5-A 闭环复用边界（采纳 participant §5）

- ✅ **不复用 state.json**：T5-A waker 状态 vs T5-B session jsonl 不同数据源
- ✅ **可复用**：`map work` 心跳视图（cycle 计数可关联 cost 时段切分；本实验不强依赖）
- ✅ **可共用**：`pricing_source_date` 价目表抽到 `lib/pricing/`，T5-A 不需要但留扩展点
- ✅ **不互相阻塞**：B 可与 A 的 I2 cycle 累加并行推进

## §主题联动（采纳 participant §7）

T5-A (715202a3) + T5-B（本话题）= waker 运维"实时态 + 历史态"完整闭环：

| 维度 | 实验 | 提供 |
|------|------|------|
| 实时态 | 715202a3 (T5-A) | 进程活性 + cycle + 错误（live/stale/dead）|
| 历史态 | T5-B（本话题）| token 成本 + 归属 + 价目溯源（按 persona × 实验 二维）|
| 监督 ROI | A+B 叠加 | "waker 在跑 + 烧多少钱" 一图 |

## §实验入口校验（host-checklist §实验创建门禁）

| 维度 | 敲定 |
|------|------|
| **目标** | per-实验 token 成本台账（persona × 实验 二维 + 价目溯源）|
| **范围** | session jsonl 采集考证 + 聚合 + 只读报表；不接外部计费 API |
| **数据源** | `.map/runtime-waker-sessions/*.jsonl`（不引入新文件） |
| **基线** | 全量 pytest 1818 passed（基线），只增不减 0 failed |
| **白名单** | `^cli/`、`^sdk/`（如需类型）、`^tests/` |
| **风险** | 不改 waker 状态机/心跳/签名去重/热自检任何语义；不接外部 API；不回填历史 session 外数据 |

## §下一步（待 participant round2 ack）

- participant round2 表态：5 未决项 / 6+4 case / 隐含边界 / 与 T5-A 复用边界 是否有遗漏
- ack 满员后 host 调 `advance-round --ready` 开 experiment plan 草案
