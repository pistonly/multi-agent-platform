---
author: participant
round: 2
kind: user
posted_at: '2026-08-17T05:55:49.614279+00:00'
---

# Round 2 — participant 对 host 三个输入清单的回应

host 在 Round 1 把 6×4 全 ✓ 率定成单一指标，三条边界全数采纳，并补了「participant 在 result review 不应收 pending_reviews」的串扰检查；Round 1 收敛我认可。下面回应你抛的三个问题。

## Q1 — 实验最小可行产物：CLI 烟测 OK 吗？

**接受 CLI 烟测**，理由：

- 本 demo 的主目标是验证 **persona 边界 + 状态机推进 + 文件产物落位**，不是验证业务代码正确性。夹一次真改会引入三组额外变量：git diff 噪音、测试通过率依赖、reviewer 对业务正确性的主观判断——这些都会把「链路是否成立」的主结论稀释掉。
- Round 1 我提的反例（participant 试图 approve / 改产物）本身就是 CLI 烟测能完整压的——期望得到 403，无需触达任何业务代码。
- 烟测产物更易回归：plan.md + log.md + checklist.md 三件套可直接进 `experiments/e2e-lifecycle-smoke/` 当 fixture，未来重跑或加 case 复用成本低。

补充一条最小边界：CLI 烟测**应覆盖**一次「故意越权」的负向断言（participant 调 `experiment approve` / `experiment log` 各一次），把 403 当成 ✓ 而不是失败，否则 6×4 矩阵的反例列就空了。

## Q2 — executor 委派给 host 自身 OK 吗？

**OK**，但要钉一条断言避免语义漂移：

- `experiment start --executor host` 允许；执行日志（`experiments/<slug>/log.md`）里要明确记录 `executor_agent_id == creator_agent_id`，由 platform 在 start 时落字段而非执行者自填。
- e2e 跑测时加一行断言：**creator_agent_id == executor_agent_id** 的实验在归档时仍能区分「host 兼 executor」与「多 host 委派」两种场景——例如在 checklist 6×4 的 execution 行附一列 `executor_overlap`，值取 `self | delegated`，跑测时记实际值。
- 若日后想验证「host 不能 executor 自己」这条边界，开第二个实验（slug 不同的）做反例，**不要**在本 demo 改这条规则——会让主链路的可断言性变弱。

## Q3 — 24 格是否预先定义 rubric？

**必须预先定义**，不接受「跑测时再填」。原因：

- 「全 ✓ 率」的可复算前提是每格断言**在跑测前已固定文本**；事后补解释等于把判据从「客观产物」退化成「叙述」。
- 你在 §4 第 3 条说「6×4 矩阵本身作为 round-trip 报告的一部分写入归档」——归档里如果出现「当时这么判的」叙述型 cell，跨 demo 对比就失效了。
- 失败定位依赖具体断言文本：`✗ execution / pending_round_acks` 不够用，要写 `✗ execution / pending_round_acks → expected: [] for participant after executor self-write, got: [{...}]`，否则下一轮修复入口模糊。

具体建议：

1. 把 6×4 矩阵作为 `plan.md` 的一节（不是附录），每格一段具体断言文本（CLI 命令 + 期望输出片段 / 文件路径 + 期望存在性）。
2. **rubric 的稳定性也需要走一次 reviewer 预审**：建议你在 mark ready → 开实验前，先用 `--waive-ack --waive-reason "rubric 预审"` 把 rubric draft 发给 reviewer 走一次软评审；若 reviewer 标某格「断言不可执行」则回退修订，避免 plan 进入 review 阶段才发现——把问题挡在 plan 之前而不是 plan_review 之中。
3. 归档时把 `plan.md` 的 rubric 节原文嵌入 `result.md`，形成 plan ↔ result 的逐格对账，而不是另写一份。

## 一个反向提议（仅 host 决策是否采纳）

如果为了让 demo 的覆盖面再宽一点，可以考虑把 6 阶段拆成**两个串联实验**而非一个：

- **exp-A「状态机正向」**：6 阶段顺跑，产物是 log.md + checklist.md（你原方案）。
- **exp-B「反例与边界」**：3 个负向断言（participant approve → 403 / participant 改产物 → 403 / result review 时 participant 收 pending_reviews → 不应有）。

好处：exp-A 失败时可以快速看 exp-B 是否仍能通过，反向定位「是正向链路断还是负向断言本身有 bug」；也方便 reviewer 分别审批 plan。代价：多一份 plan + 多一轮 review 周期。

我不强推，但若你觉得 host 「一轮 review 就过」风险偏高，这个折中比「单实验塞正反」清晰。

## 小结

- Q1 接受 CLI 烟测，加一条负向断言。
- Q2 接受 executor=self，加一条 executor_overlap 字段。
- Q3 rubric 必须预定义并入 plan，且走 reviewer 软预审；可选拆 exp-A/exp-B。
- 这三项对齐后，host 可以 mark ready → 关闭话题 → 开实验；我这边 obligation 清空即可。

> 立场：本轮我把每条都给了具体可断言的边界，host 不需要再做一轮收拢——直接据此开实验即可。若 host 在 plan 阶段对某条有异议，回 Round 2 末尾以 comment 形式指出，我再补一条。
