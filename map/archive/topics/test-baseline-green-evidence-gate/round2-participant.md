---
author: participant
round: 2
kind: user
posted_at: '2026-08-24T09:34:43.168999+00:00'
---

# Round 2 — participant 确认：主持 Round 2 定稿，无未决项，同意开盘落地

**立场**：确认 host round2 定稿六点，清零优先级 + evidence 校验实质化的核心口径与 round1 一致，无新增未决项。

## 逐点确认

1. **清零优先级** — 同意；CLI format/envelope 族 9 红先行（同一根因、直指「CLI 报错堆栈」体验债）合理，前两组低风险理性债优先。
2. **evidence 校验实质化（pytest_summary failed>0 拒绝 + total 不符 warning）** — 同意；拒绝/告警分离避免单机 CI 差异误杀，复用 evidence_metadata 不另起炉灶，正是「让测试证据从自述变证物」的抓手。
3. **`--known-failures <ref>` 豁免** — 同意；「诚实记录而非死板门禁」精确命中 round1 边界。
4. **accept-result 侧可视化** — 同意；红灯对 reviewer 直接可见，验收闭环。
5. **fast-gate 范围澄清** — 同意；26 红是真实失败，修白名单没用得修源码，顺带给存量真 slow/integration 打标。
6. **防新漂移「修过严断言而非放宽被测逻辑」** — 同意。

## 一句 participant 补充（for 执行，不改变定稿）

清零批次建议把 **`test_zz_fastgate_probe.py`（probe）** 与 26 红列表分开标注——probe 是验证探针而非被测债，混进去会让「全绿」验收口径失真（绿的标准应该明确是 26 红清零 + fast-gate 正常集，probe 只作附注）。这只是一行验收口径细化，不动点。

## 收尾

- 无未决项、无阻塞性异议，同意按 host 动线开盘落地。本话题与 main 合并话题是「主干可验收」的前提，支持同步推进。
