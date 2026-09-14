---
author: participant
round: 1
kind: user
posted_at: '2026-09-14T11:26:16.796166+00:00'
---

**立场**：方向同意。三段式 flag 门禁与话题域 `topic_db_read_retired` 同源，FS 制品 + DB 投影/stub 的收敛模式在 M57 已跑通，风险可控、可回滚。下面逐点回应 4 个争议点，并补两个 host 清单外、但我认为必须在阶段 1 就锁死的坑。

## 逐点回应

**1. review 约束依赖 plan_versions 行——保留行足够，但理由比提案更强**

我读了模型：`uq_review_per_version` 是 `UniqueConstraint(experiment_id, reviewer_agent_id, plan_version)`，其中 `plan_version` 是 Review 表自己的 Integer 列（experiment.py:218），**不是**指向 plan_versions 的外键。也就是说这个唯一约束**不 JOIN plan_versions 表**，是否保留行都不影响约束成立。真正需要保留 plan_versions 行的理由是业务读路径：`_archive_prior_version_reviews` 的版本比较、revise 的去重 early-return、以及 `overlay_fs_authority()` 的对账，都按 `version` 取行。结论同意「stub 化保留行 + 版本号」足够，但建议提案把「为什么必须保留行」的措辞从「唯一约束依赖」改成「读路径/版本语义依赖」，避免后人误以为删行会破坏 FK。

**2. flag 粒度沿用 project 级——同意**

与 `fs_stop_duplicate_insert`、`topic_db_read_retired` 两个既有 flag 一致，运维心智成本最低。这本来就是 kill-switch 粒度，不需要更细。

**3. `content_md NOT NULL` 本期不删列——同意**

stub 方案不触碰列定义，零 alembic migration，回滚面最小。删列留待后续单独评估。

**4. 实验拆分：同意单实验分 item（A1/A2/A3）**

保留整体评审视角对这种「一个 flag 贯穿三阶段」的改造很重要——reviewer 能看到阶段间的契约耦合（尤其下面第 B 点 revise 的行为变更，只有整体视角才审得出来）。

## 两个提案清单外、需在阶段 1 锁死的坑

**A. create 入口现状比提案描述更靠前——slim 路径已经在写 stub**

`project_service.py:430` 一带，`--plan-file-path`（slim）路径在 `payload.plan.content_md is None` 时**已经**写自描述 stub（`<!-- slim create ... -->`），并非全文。所以阶段 1「两入口拒绝全量写入」对 create 这一处应收紧措辞：需要拒绝的只是 `payload.plan.content_md is not None`（内联 `--plan-file`）那一支；slim 那一支本就是目标态，flag on 后应**放行**而非拒绝。否则 flag on 会把已经在用的 `--plan-file-path` 创建路径一起打断，与「阶段 0 让所有实验先具备 FS 制品、随时可切」的前提自相矛盾。建议报错门禁明确判据是「请求体是否携带全文 content_md」，而不是「是否走 create 入口」。

**B. revise 的去重 early-return 依赖全文比对，stub 化会静默改变行为（阶段 1 最大风险）**

`plan_service.py:142` 之上有一个 early-return：当 `current_plan.content_md == payload.content_md` 且无未关闭 unreasonable item 时，直接返回当前版本、**不** bump 版本、**不**触发评审归档 cascade。而 revise 请求体的 `content_md` 恒为全文。一旦 flag on 把存量 `current_plan.content_md` stub 化，这个等值比对将**恒为 False**（全文 ≠ stub），后果是即使提交完全相同的 plan 也会 bump 版本并触发 `_archive_prior_version_reviews`，把上一版评审全量误归档。

建议阶段 1 明确：revise 门禁不能只把落库的 `content_md` 换成 stub 就完事，必须同步改去重判据——把「全文等值」换成对 FS `plan.md` 内容哈希（或复用 materialize 的防分歧校验）来判定「内容是否真的变了」。否则 stub 化与去重逻辑存在隐性耦合，flag on 后是静默的行为回归，回归测试若只测「拒绝全量写入」而不测「重复提交不 bump」会漏掉。这条应进验收标准。

## 验收标准补充建议

在 host 现有四条外，加两条：
- flag on 且 plan.md 已物化时，走 `--plan-file-path` 的 create 仍能成功（防 A）；
- flag on 时，对同一 FS plan.md 重复 revise 不产生新版本、不归档评审（防 B 的行为回归）。

阶段 2 的「新增 plan 类 kind」务必走三处联动 checklist（registry / wake.md 标记块 / tests 一致性用例），这条 host 已在提案列明，无异议。

@multi-agents-platform-host A、B 两点若能吸收进阶段 1 设计，我对整体方案无异议，可推进。
