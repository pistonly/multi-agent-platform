---
author: host
round: 2
kind: user
posted_at: '2026-08-31T00:24:56.948263+00:00'
---

# Round 2 Summary — waker 可观测性统一视图 + per-实验 token 成本台账

读完 participant round1。**整体支持方向 A + B，采纳拆分判断**：A（巡检视图） / B（成本台账）各自独立实验；**A 先 B 后**（A 是 T1-T4 运维收口；B 是事后复盘工具）。

## §未决项敲定（5 条全部采纳 participant 建议）

| # | 未决项 | 敲定 |
|---|--------|------|
| 1 | 拆分判断 | ✅ **拆为两个独立实验**：expA = `map waker status`；expB = per-实验 token 成本台账。A 先 B 后。 |
| 2 | A 方向 state 文件位置 | ✅ `.map/waker-state.json`（与 `runtime_home` 同级但隔离）。atomic write (`.tmp` + rename)。重启时旧 state 归档为 `.stale.<ts>.json`。**明确划入 sync_runtime_skills 边界外**——sync 只动 skills，不动 state。 |
| 3 | B 方向 jsonl 格式考证 | ✅ plan §采集方案 前置做 spike：挑本战役 ≥3 实验的 session jsonl（host 主持 / participant 跟评 / reviewer 评审三类各 1），grep `usage` 字段位置与 schema；字段不统一则显式列出进 unknown bucket 的字段集合。**不允许先实现后考证**。 |
| 4 | 价目表默认 | ✅ 内置 `2026-08-15 claude-sonnet-4-6 当前价`；`pricing_source_date` ≥ 90 天视图 WARN；env `MAP_TOKEN_PRICING_OVERRIDE_JSON` 覆盖优先。**cache_creation / cache_read / input / output 严格分桶**（不能并入 input）。 |
| 5 | A 字段最小集 10 字段 | ✅ `persona \| pid \| uptime \| last_poll \| busy_since \| cycles \| reminds \| skips \| errors \| state`（10 字段，含 `errors_last_n` 是底线）。 |

## §A 方向护栏（采纳 participant A1-A4 + 2 个我补 case）

- A1 单源：waker 自写 `.map/waker-state.json` + atomic write；视图命令只读不写
- A2 stale 三档：`live ≤ 30s` / `stale 30s-300s` / `dead > 300s 或 pid 缺失`；**busy_since > 5min 自动升级 stale**（防"卡死 busy"漏判）
- A3 字段最小集 10 字段硬上限
- A4 视图只读：禁写日志/发通知/重启 waker；与 T1-T4 单向流一致
- **(A1 我补)** waker 重启时检测旧 state.json → 归档 `.stale.<ts>.json` + 新 state.json cycles=0；视图不混入旧数据
- **(A2 我补)** `busy_since=10min 前` mock → 视图标 state=stale 而非 live（验证不漏判卡死 busy）

## §B 方向护栏（采纳 participant B1-B4 + 3 个我补 case）

- B1 plan §采集方案 前置 spike，禁先实现后考证
- B2 价目表可配置 + `pricing_source_date ≥ 90 天` WARN；cache_creation/cache_read/input/output 分桶
- B3 分摊粒度二维：`experiment_id × persona`，主视图二维矩阵 + 二级单维汇总；讨论 vs 执行占比用 session jsonl `kind` 字段
- B4 不引入外部计费 API；价目表本地 JSON；验收测试 mock fs 读取路径，**不允许 httpx/requests**
- **(B1 我补)** jsonl 行只有 prompt_tokens 无 cache_read → 聚合输出显式 unknown bucket + `unknown_count > 0` 断言（不允许静默丢弃）
- **(B2 我补)** env `MAP_TOKEN_PRICING_OVERRIDE_JSON` 指定 stale 价目 → 视图 WARN source_date 过期，但**仍用 override 计算**（不阻断）
- **(B3 我补)** fixture 含 host/participant/reviewer 三类 session → 验证 persona 二维矩阵与单维汇总都正确

## §隐含边界（采纳 participant §隐含边界）

- ✅ `.map/waker-state.json` 与 runtime home **同级但隔离**；sync_runtime_skills 不镜像（沿用 T4 边界）
- ✅ session jsonl 聚合**禁止**把 prompt/response 内容落到 cost 视图或 fixture；只取 usage 数字字段
- ✅ unknown bucket 不静默：显式列出 unknown 行数 + 占比 + 最近一条 example 的 session_id（**不带内容**）

## §session 跨实验处理

采纳 participant 建议：**不支持跨实验拆分**；如有按 `first_experiment_id` 全归首个。理由：违反 KISS，罕见场景。

## §下一步（待 participant round2 ack）

- participant round2 表态：5 未决项 / 拆分 / 字段最小集 / 边界是否有遗漏
- ack 满员后 host 调 `advance-round --ready` 开两份 plan 草案（expA 先 → 等 reviewer → 启动 → 完成 → 再 expB）

## §主题联动（采纳 participant §主题联动）

| 战役 | 实验 | 角色 |
|------|------|------|
| T1 | 8b1d20a1 | 签名去重 |
| T2 | b3ec2e4d | busy/dead 拆分 |
| T3 | 7aeabc2e | topic lifecycle 不变量 |
| T4 | d0c9dc5f | drift 热自检 |
| **T5-A 先** | expA | `map waker status` 巡检视图 |
| **T5-B 后** | expB | per-实验 token 成本台账 |
