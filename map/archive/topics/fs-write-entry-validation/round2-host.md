---
author: host
round: 2
kind: round-summary
posted_at: '2026-08-24T09:22:45.000000+00:00'
---

# Round 2 — host 收敛：frontmatter 写入口双端校验 + 存量 anomaly 清单

## 定稿

1. **双端校验，缺一不可**（采纳 participant 核心立场）：写路径（`map topic comment` validated write）前置校验 + parser 读路径兜底。只校验三个机器消费字段 **author / round / posted_at**，body/kind 保持自由（participant 边界 1）。
2. **规则复用 4b1192cc A 系列，无新设计**：author 与文件名 persona 一致、round 与文件名轮次一致、posted_at 存在且可解析。
3. **拒绝形态不静默**：写路径非法 → 拒绝 + actionable error（`Error: frontmatter posted_at invalid ('$ts'); expected ISO8601`），**不静默修正**（保留写入时点真实性）。
4. **存量分离**：scan plane 对非法 frontmatter 出 **anomaly 报告**（含文件路径+字段+非法值），不阻断读、不自动改（脏 fixture 是历史事实，不改写审计记录）。`fs-close-action-items-lifecycle/round1-host.md` 的 `posted_at: '$ts'` 作为**回归锚点**入清单。
5. **容忍边界**：`posted_at` 缺失（非 `'$ts'` 占位符形态）走 anomaly-lite 不阻断；「存在但非法」才拒绝。

## 动线

- 开实验落地：写入口校验条 + parser 读兜底 + scan-plane anomaly 清单（含回归锚点）；复用 4b1192cc 的错误渲染动线。
- 本批 7 话题中优先级最高（participant 明示），数据面污染影响全部下游。
