---
author: host
round: 1
kind: user
posted_at: '2026-08-31T00:21:19.349748+00:00'
---

# T5：waker 可观测性统一视图 + per-实验 token 成本台账

> 本话题较大，覆盖两个方向；讨论阶段可拆分为两个独立实验。

## 现象（实测）

本战役（T1 扇出收窄 → T4 skill 热自检）全程由监督者 30 分钟巡检，两个系统性缺口：

1. **无统一 waker 状态视图**：每次巡检要手工拼四件套——`pgrep` 数进程、`map work --notification-category wakeable` 看心跳/busy、`tail .map/waker-logs/*.log` 看 cycle/remind 统计、`pstree` 查忙会话。T2 落地后 `map work` 已有心跳+busy，但 waker 进程活性（pid/启动时长/cycle 计数/remind_sent/skips/errors）仍无单一入口。
2. **per-实验成本是黑盒**：每个实验消耗多少 token、哪个 persona 烧的、讨论 vs 执行占比，全部无记录。runtime session jsonl（`.map/runtime-waker-sessions/*.jsonl`）是原始素材，但无聚合工具；想回答「这个实验烧了多少钱」只能人肉翻 jsonl。

## 任务

### 方向 A：`map waker status`（或等价命令）

单命令输出：各 persona 进程活性（pid/uptime）+ 心跳/busy 状态（复用 T2 数据）+ 最新 cycle 统计摘要（cycles/reminds_sent/skips_unchanged/errors，从 waker 日志或 state 文件读）。只读视图，不改任何写路径。

### 方向 B：per-实验 token 成本台账

从 runtime session jsonl 聚合 token usage（input/output/cache），按 persona × 实验（或话题）维度出台账；落点可以是 `map experiment show --cost` 或独立报表命令。若 session jsonl 的 usage 字段不完整，先考证 claude runtime 输出格式再定采集方案，缺字段则补记录而非估算。

## 机器可判验收要求

```bash
# 1. 新增回归测试（必须）：
#    A: waker status 渲染——进程活/死、busy/stale、cycle 统计三态 fixture
#    B: 成本聚合——构造含 usage 的 session jsonl fixture，验证 per-persona/per-实验聚合正确；
#       缺 usage 字段的记录被显式计入「未知」而非静默丢弃
# 2. 全量测试绿（基线 1791 passed / 2 skipped / 359 deselected，只增不减，0 failed）
.venv/bin/python3 -m pytest tests/ -q
# 3. git diff --name-only 白名单：^cli/、^server/（如需）、^sdk/（如需）、^tests/
```

## 边界

- 方向 A 只读，不改 waker 状态机/心跳/签名去重任何语义。
- 方向 B 不引入外部计费 API；仅以本地 session jsonl 为数据源。token→金额换算若有默认价目表，必须可配置且标注来源日期。
- 两个方向若讨论后确认拆分，各自独立实验、独立验收。
- 涉及 cli//server/ 改动，验收通过后由监督者重启 server 与 waker 生效。
