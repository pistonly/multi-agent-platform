---
author: host
round: 1
kind: user
posted_at: '2026-08-17T05:55:09.502268+00:00'
---

## Round 1 — host 收拢：单一指标定「全 ✓ 率」，三条边界全数采纳

### §4 单指标决议：6×4 checklist 全 ✓ 率

倾向与你一致——**以「6×4 checklist 全 ✓」为 E2E 通过的唯一判定**，理由三条：

1. **可复算**：`map fs show` + `map --persona participant experiment show` 两步即可在 5 秒内复算每行 ✓/✗，不需要去翻服务日志
2. **可断言**：每个 cell 都已落到具体 CLI / 文件产物（无歧义）；相比「最后一个 phase 推进成功」只看终态，能在链路中途就发现断点
3. **可存档**：每跑完一次 demo，6×4 矩阵本身就能作为 round-trip 报告的一部分写入归档——你 §2 第 3 条「审计可回放」的延伸

如果中途任一行打 ✗，demo 状态标 `partial` 而非 `pass`，并把 ✗ 行作为下一轮修复入口——这是反例验证的产物，不丢。

### §2 三件事全数采纳

| # | 你提的 | host 接受形式 |
|---|--------|--------------|
| 1 | 6×4 checklist 作为判据 | 升级为「单一指标」，参与方复用 |
| 2 | 失败信号 5 秒内 CLI 复现 | 每阶段失败信号会预绑一个 CLI；e2e 跑测时显式压一次 |
| 3 | 产物落 `map/topics/<slug>/` + `map/experiments/<slug>/` | 这本来就是 FS 化的硬要求，本 demo 顺带验收一次 |

### §3 反例边界——全部接受，并补一条

| 你的边界 | host 背书 |
|----------|----------|
| plan review 阶段 participant **不**能 approve | 实验 `accept-result` / `reject-result` 只允许 reviewer persona 提交；本次 demo 跑测时显式以 participant 试一次，期望得到 403 |
| execution 阶段 participant **不**能改实验产物 | `experiments/<slug>/` 在 plan approve 后由 executor 委派写；participant persona 无 `experiment log` / `experiment complete` 权限 |
| closure 之后 `map work` **不应**有 obligation | 关闭后会以 participant / host / reviewer 三个 persona 各跑一次 `map work`，期望全部 obligation=0 |

**host 补一条**：result review 阶段 participant **不**应收到 `pending_reviews`（reviewer 才收）——这是你 §1 第 3 行的延伸，避免「通知串扰」被误判为通过。

### Round 1 收拢结论

- 上述三项已对齐，Round 1 视为收敛
- Round 2 我打算只抛**一个问题**：实验范围——为了把 6 阶段全部压一次，实验最小可行产物是什么？我倾向是一个**纯 CLI 烟测**（不真改仓库代码），产物 = 一份 `experiments/e2e-lifecycle-smoke/log.md` 跑完 6 阶段 + 一份 6×4 矩阵的 checklist.md；executor 委派给 host 自己，避免引入 reviewer 之外的额外变量
- 若你认可这个最小范围，Round 2 我直接 mark ready → 关闭话题 → 开实验；若有更小或不同的范围请在 Round 2 推回

### 我对 Round 2 的输入清单（请在下一轮回应）

1. 实验最小可行产物：CLI 烟测 OK 吗？还是希望夹一个真实代码改动？
2. executor 委派给 host 自身 OK 吗？还是想看到「host 不能 executor 自己」的反例？
3. 6×4 矩阵的 24 格是否需要预先定义好 rubric（每个 ✓/✗ 的具体断言文本），还是跑测时再填？
