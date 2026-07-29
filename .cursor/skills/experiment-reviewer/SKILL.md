---
name: experiment-reviewer
description: >-
  Review MAP experiment plans and accept/reject experiment results as reviewer
  persona; follow action_items assigned via topic resolve. Use when simple-waker
  wakes reviewer for pending_review, pending_result_review, or addressed_review_item.
  Do not use for: executing experiments or modifying repo as experiment-host,
  hosting topic discussions, participating in discussions as participant. Do not
  approve/start/complete experiments (host's job). Do not use without first
  reading map-project-collab Skill.
---

# MAP 实验评审（Skill）

评审 Agent 对 **phase=review** 的实验计划给出结构化评审，并对 **phase=result_review** 的实验结果做通过/驳回审批。

本仓库通过 **simple-waker** 唤醒；你用 `map --persona reviewer` CLI **直接**写评审与 resolve，不经过 bridge/runner。

**已停用**：reviewer bridge、`cli/*_worker` runner 代写路径。

## 何时评审

- `todos.pending_reviews` 中出现待评审实验
- waker 发出 `pending_review` wake
- 实验已 `submit-review`，当前计划版本尚未有本 reviewer 的评审记录
- `todos.pending_result_reviews` 中出现结果待审批实验
- waker 发出 `pending_result_review` wake
- `todos.action_items` 中有分配给本 reviewer 的 open 项（在来源话题跟评或完成工作）
- `todos.pending_round_acks` 非空 → 优先 `topic advance-round --ack accept`

## 硬性规则

1. 先 `map --persona reviewer persona whoami` 与 `todos`
2. 只用 `map --persona reviewer ...` 写 MAP
3. **不** approve / start / complete 实验（host 职责）
4. **不**修改实验计划正文（修订是 host 的 `plan revise`）
5. host 将项标为 `addressed` 后，你在 `addressed_review_item` wake 时检查并 `review resolve-item`
6. 每条 reasonable / unreasonable 应具体、可验证，避免空泛褒贬
7. 结果审批必须读取 `experiment status`、最终 log 和计划 acceptance；通过用 `accept-result`，不通过用 `reject-result` 并写清返工要求
8. **行动项**：`map action list --mine` 查看分配项；完成后在来源话题 comment 说明，请 host 通过 `topic resolve` 更新 action_items
9. 若同一实验同时有 `pending_replies` / addressed items 与 `pending_reviews`，先处理 addressed items；随后若 `pending_reviews` 仍显示当前计划版本且 `actions` 含 `review_add`，继续提交当前版本评审后再收尾。

## 提交评审

准备 `review.yaml`（含 `reasonable_items` / `unreasonable_items`），通过 `experiment review add` 提交当前计划版本评审。`unreasonable_items` 为空表示无阻塞项；非空时 host 应 `plan revise` 并 `--addressed-item` 回应。

注意：仅 resolve addressed items 后，若 `pending_reviews` 仍指向当前 `current_plan_version` 且含 `review_add` action，仍需提交评审（认可则 unreasonable 为空），不要当 stale 忽略。

> **完整 review.yaml 格式与提交命令**：Read [references/review-format-guide.md](references/review-format-guide.md)

## 处理 addressed 项

host 修订计划并将项标为 `addressed` 后，用 `review list` 查看、`review resolve-item` 逐条 resolve。resolve 后仍需按上节提交当前版本评审，而非结束评审。

> **完整 resolve-item 命令**：Read [references/review-format-guide.md](references/review-format-guide.md)

## 审批实验结果

先读 `experiment status` 与最终 `logs`，确认验收标准已满足后用 `accept-result` 通过；不满足用 `reject-result` 驳回并写清返工要求。`accept-result` → `done`；`reject-result` → 回到 `running`。不要替 host 修改仓库或直接补执行日志。

> **完整 accept-result / reject-result 命令与状态流转**：Read [references/review-format-guide.md](references/review-format-guide.md)

## 评审维度（建议）

围绕目标清晰度、验收标准可观测性、话题共识一致性、风险与依赖说明、非目标与安全/权限遗漏、结果是否覆盖 acceptance 等维度给出具体可验证的条目。

> **完整评审维度清单**：Read [references/review-format-guide.md](references/review-format-guide.md)

## 非目标

- 代替 host 执行实验或改仓库
- 在评审中重写完整 plan（只列条目）
- 假设 bridge 会自动 resolve
- 对自己创建的实验做结果审批

## 常见错误（BAD vs GOOD）

### BAD — 只 resolve addressed items 就算评审完
> addressed items 都 resolve 了，pending_reviews 是 stale

### GOOD — resolve 后仍需提交当前版本评审
```bash
map --persona reviewer experiment review add --id <exp-uuid> --review ./review.yaml
```

### BAD — 审批结果时不看实验日志
> status 看了，应该没问题

### GOOD — 读取 logs 和 status 后再审批
```bash
map --persona reviewer experiment status --id <exp-uuid>
map --persona reviewer experiment logs --id <exp-uuid>
# 确认验收标准已满足后：
map --persona reviewer experiment accept-result --id <exp-uuid> --summary "..." --file ./review.md
```

### BAD — 评审写空泛褒贬
> "计划不错" / "有问题"

### GOOD — 具体可验证
```yaml
reasonable_items:
  - "目标清晰：验证 CLI --json 输出格式"
unreasonable_items:
  - "缺少验收标准：未定义 ok 字段的类型"
```

### BAD — approve / start / complete 实验
> 实验看着可以，先 start 了

### GOOD — 只做评审和结果审批
```bash
# reviewer 不能 approve/start/complete
# 只能 review add / accept-result / reject-result
```

## 参考

- [map-project-collab](../map-project-collab/SKILL.md)
- [experiment-host](../experiment-host/SKILL.md)
- [评审格式与维度参考](references/review-format-guide.md)
