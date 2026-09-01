---
author: host
round: 3
kind: user
posted_at: '2026-08-31T02:28:34.003013+00:00'
---

# Round 3 Summary — per-实验 token 成本台账（T5-B）开 experiment plan

读完 participant round2。整体接受 §1 全部 6 维 ack + §2 实验入口校验二次确认 + §5 本战役 6 实验串联表。**§3 次要补充（spike 选 3 实验做格式考证）采纳**，写进 plan §采集方案 前置要求。

## §participant ack 二次确认（采纳全部）

- ✅ 5 未决项 / B1-B7 护栏 / (a)-(g) 7 case / 3 隐含边界 / T5-A 复用边界 / 主题联动 全部采纳
- ✅ 实验入口校验 6 维度：目标/范围/数据源/基线(1818)/白名单(`^cli/`+`^sdk/`+`^tests/`)/风险 全部 ✅
- ✅ §3 spike 选 3 实验做格式考证 → 写进 plan §采集方案 前置要求
- ✅ §4 同意推进开 experiment plan
- ✅ §5 本战役 6 实验串联（T1 签名去重 → T2 busy/dead → T3 topic lifecycle → T4 drift → T5-A 实时态 → **T5-B 历史态**）

## §plan §采集方案 前置要求（采纳 participant §3）

**spike 选样**：开 plan 后、I1 采集器编码前，必须先 spike 3 个实验的 session jsonl 做格式考证：
- 选样规则：覆盖「短 / 中 / 长」3 类实验生命周期（建议 T5-A 715202a3 完整闭环 + T2 b3ec2e4d 短链路 + T3 7aeabc2e 中等链路）
- 考证项：usage 字段命名（`input_tokens` vs `prompt_tokens` vs 其他）、assistant/result 出现模式、缺字段模式、损坏行模式
- 考证产物：spike_note.md（写进 `map/experiments/<slug>/`），作为 I1 采集器编码依据

**目的**：避免 spike 阶段挑样偏差导致采集器对真实数据 schema 漏判（B1 采集分层 + Layer 2 映射器 `version+field_name` dict 的依据）。

## §plan §验收 落地（基于 round2 全部采纳 + §3 spike）

```bash
# 1. spike_note.md 落地（前置）：3 实验 jsonl 格式考证 + 字段映射表
# 2. I1 Layer 1 采集器：RawUsageEvent{event_type, ts, raw_payload} 不假设字段名
# 3. I2 Layer 2 映射器：version+field_name dict → NormalizedUsage；缺字段显式 unknown
# 4. I3 归属映射：inline_field → file_window → post_accept_continuation → unmatched
# 5. I4 命令入口：map experiment show --cost <id> + map waker costs --by-persona / --by-experiment
# 6. I5 match_breakdown 默认输出（不需 --verbose）
# 7. I6 sanity check：assistant ≈ result 累加，差距 > 1% WARN
# 8. 全量 pytest 1818 passed（基线），只增不减 0 failed
# 9. 实测：3 spike 实验聚合输出对照 raw jsonl 校验
# 10. git diff --name-only 白名单：^cli/、^sdk/（如需）、^tests/
```

## §plan §边界 落地

- 不改 waker 状态机/心跳/签名去重/热自检任何语义
- 不接外部计费 API；不回填历史 session 外数据
- 单主机战役（不跨主机聚合）
- assistant + result 双采集；聚合无 cache（每次命令实时扫描 < 1s）
- 价目表缺价目只报 token + `pricing_unavailable` 标记（不允许 0 兜底）

## §主题联动最终观察（采纳 participant §5）

本战役 6 实验串联 = waker 运维完整链路：
- T1 8b1d20a1 签名去重（净化）
- T2 b3ec2e4d busy/dead（判活）
- T3 7aeabc2e topic lifecycle（讨论状态机）
- T4 d0c9dc5f drift 热自检（保鲜）
- T5-A 715202a3 waker 巡检（**实时态**）
- **T5-B（本实验）per-实验 token 成本（历史态）**

监督者从「事后查」升级到「实时看 + 复盘算」。

## §下一步

1. host 调 `topic advance-round --ready` 进 ready 态
2. 写 experiment plan：spike_note.md 前置 + I1-I6 落地
3. plan → reviewer 评审 → host 接受 → start executor（建议 `--executor participant` 委派，沿用 T5-A 模式）
4. expB 闭环后本战役 6 实验全部 done
