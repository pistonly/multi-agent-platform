---
author: host
round: 1
kind: user
posted_at: '2026-08-31T01:52:15.668162+00:00'
---

# T5-B：per-实验 token 成本台账

> 承接已关闭话题 `waker-status-and-cost-ledger` 的 round2 共识：方向 A（waker status 视图）已由实验 715202a3 落地；本话题聚焦方向 B。

## 现象（实测）

本战役 5 个实验（T1 扇出收窄 → T5-A 状态视图）的 token 成本全程无记录：每个实验烧了多少 input/output/cache token、哪个 persona 烧的、讨论 vs 执行占比，全部只能人肉翻 `.map/runtime-waker-sessions/*.jsonl`。监督者实测过：session jsonl 顶层无 usage 字段，usage 藏在 claude stream-json 的具体事件里（result/assistant message），需要正式考证采集口径。

## 任务

1. **采集考证**：确认 claude runtime（stream-json 输出）各事件的 usage 字段位置与口径（input/output/cache_read/cache_creation），写成测试 fixture 依据；缺 usage 的事件显式计「未知」，不静默丢弃、不估算。
2. **聚合台账**：按 persona × 实验（或话题）维度聚合 token 数；落点优先 `map experiment show --cost` 或独立只读命令（如 `map waker costs`）；session→实验的归属映射方案讨论收口（session 文件名含 persona+时间戳，实验窗口可界定）。
3. 窄提交白名单：`^cli/`、`^sdk/`（如需类型）、`^tests/`。

## 机器可判验收要求

```bash
# 1. 新增回归测试（必须）：
#    - 构造含 usage 的 session jsonl fixture → per-persona/per-实验聚合正确（含 cache 字段）
#    - 缺 usage 的记录计入「未知」计数且输出中显式标注
#    - 空目录/损坏行 → 不崩，告警
# 2. 全量测试绿（基线 1806 passed / 2 skipped / 359 deselected，只增不减，0 failed）
.venv/bin/python3 -m pytest tests/ -q
# 3. git diff --name-only 白名单：^cli/、^sdk/、^tests/
```

## 边界

- 纯只读分析，不改 waker 状态机/心跳/签名去重/热自检任何语义。
- 不接外部计费 API；token→金额若有默认价目表必须可配置且标注来源日期，缺价格就只报 token 数。
- 不回填历史 session 以外的数据；session jsonl 缺字段的实验标注「未知」。
- 涉及 cli/ 改动，验收通过后由监督者重启 server 与 waker 生效。
