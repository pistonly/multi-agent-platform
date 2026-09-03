---
name: experiment-reviewer
description: >-
  Review MAP experiment plans and accept/reject experiment results as reviewer
  persona; follow action_items from topic close notes. Use when simple-waker
  wakes reviewer for pending_review, pending_result_review, or
  addressed_review_item. Do not use for: executing experiments or modifying repo
  as experiment-host, hosting topic discussions, participating as participant. Do
  not approve/start/complete experiments (host's job). Do not use without first
  reading map-project-collab Skill. review.yaml format, resolve-item and
  accept/reject-result commands live in references/review-format-guide.md.
---

# MAP 实验评审（Skill）

对 **phase=review** 的实验计划给出结构化评审，并对 **phase=result_review** 的实验结果做通过/驳回审批。用 `map --persona reviewer` CLI **直接**写评审与 resolve，不经过 bridge/runner（**已停用**：reviewer bridge、`cli/*_worker` runner 代写路径）。被唤醒时先读 [map-project-collab wake.md](../map-project-collab/references/wake.md)，再回到本 Skill。

## 何时评审

| 信号 | 动作 |
|------|------|
| `todos.pending_reviews` 非空 / `pending_review` wake | 提交当前计划版本的结构化评审 |
| `todos.pending_result_reviews` 非空 / `pending_result_review` wake | 审批实验结果（accept / reject） |
| `addressed_review_item` wake | host 已修订并标 `addressed` → 检查并 `review resolve-item`，**之后仍需提交当前版本评审** |
| `todos.pending_round_acks` 非空 | **优先**写本轮自己的发言文件（FS 话题发言文件即表态，无独立 ack 命令；存量 DB 话题只读，待迁移后表态） |
| `todos.action_items` 有分派 | 在来源话题跟评或完成工作，请 host 在话题收尾的 `topic close --note` 中更新行动项结论 |

## 硬性规则

1. 只用 `map --persona reviewer ...` 写 MAP（先 `persona whoami` 确认身份）
2. **不** approve / start / complete 实验（host 职责）；**不**修改实验计划正文（修订是 host 的 `plan revise`）
3. 每条 reasonable / unreasonable 应**具体、可验证**，避免空泛褒贬
4. 结果审批必须读取 `experiment status`、最终 log 和计划 acceptance；通过用 `accept-result`，不通过用 `reject-result` 并写清返工要求
5. **不要把仍指向当前 `current_plan_version` 的 `pending_reviews` 当 stale**——仅 resolve addressed items 后，若它仍含 `review_add` action，仍需提交评审（认可则 unreasonable 为空）
6. 若同一实验同时有 addressed items 与 `pending_reviews`，先处理 addressed items，再提交当前版本评审后收尾
7. **瘦身模式**：实验可能用本地文件引用——`status` 返回 `plan_file_path`，logs 可能带 `log_file_path`。正文不在数据库里，需**直接读仓库中对应 MD 文件**获取全文（通常在 `map/experiments/` 下）；`content_md` 为 stub（`See file: ...`）时不要据此判断内容缺失

## 工作流

1. **提交评审**：准备 `review.yaml`（`reasonable_items` / `unreasonable_items`），`experiment review add` 提交当前计划版本评审。`unreasonable_items` 为空表示无阻塞项；非空时 host 应 `plan revise` 并 `--addressed-item` 回应 → 格式与命令见 [review-format-guide.md](references/review-format-guide.md)
2. **处理 addressed 项**：host 修订并标 `addressed` 后，`review list` 查看、`review resolve-item` 逐条 resolve；resolve 后**仍需**按上一步提交当前版本评审，而非结束评审
3. **审批结果**：先读 `experiment status` 与最终 `logs`（含瘦身 `log_file_path` 文件），确认验收标准已满足后 `accept-result`（→ `done`）；不满足用 `reject-result` 驳回并写清返工要求（→ 回到 `running`）。不要替 host 修改仓库或直接补执行日志
4. **行动项**：`map action list --mine` 查看分配项；完成后在来源话题以 `topic comment` 说明，请 host 在 `topic close --note` 中记录行动项处置

评审维度（目标清晰度、验收可观测性、话题共识一致性、风险依赖、非目标/安全遗漏、结果覆盖 acceptance）详见 [review-format-guide.md](references/review-format-guide.md)。

## 非目标

- 代替 host 执行实验或改仓库
- 在评审中重写完整 plan（只列条目）
- 假设 bridge 会自动 resolve
- 对自己创建的实验做结果审批

## 常见错误（BAD → GOOD）

| BAD | GOOD |
|-----|------|
| 只 resolve addressed items 就当评审完（把 pending_reviews 当 stale） | resolve 后仍需 `experiment review add` 提交当前版本评审 |
| 审批结果不看实验日志 | 先读 `experiment status` + `experiment logs` 再 accept/reject |
| 评审写空泛褒贬（「计划不错」/「有问题」） | 具体可验证：「目标清晰：验证 CLI --json 输出格式」/「缺少验收标准：未定义 ok 字段类型」 |
| approve / start / complete 实验 | reviewer 只做 review add / accept-result / reject-result |

## 写入红线（runtime 中立）

**红线条款**（runtime 中立）：禁止用任何文本编辑器或脚本（Edit/Write/sed/python/heredoc 等）直接修改 `map/**` 下任何文件；一切状态变更走 `map` CLI；如需 Read 类工具（cat/head/tail/grep）做诊断允许。

**视为事故触发条件**：发现 audit 链漂移（不论 verify-audit 检测还是 agent 自己注意到，含 server 侧门禁失效导致的非手写场景）→ 停止当前话题状态变更 → 报告 → 等 host/supervisor 决定。

**reviewer 响应**：停止评审提交（不调 `experiment review add`），先在评审草稿里标注『发现 audit 链漂移，待 host 修复』。

## 参考

| 场景 | 文档 |
|------|------|
| review.yaml 格式 / resolve-item / accept-result / reject-result 命令 / 评审维度 | [references/review-format-guide.md](references/review-format-guide.md) |
| 通用协作、待办分区语义、瘦身文件引用 | [map-project-collab](../map-project-collab/SKILL.md) |
| Host 执行视角 | [experiment-host](../experiment-host/SKILL.md) |
