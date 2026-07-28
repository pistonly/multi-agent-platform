---
name: experiment-reviewer
description: >-
  Review MAP experiment plans and accept/reject experiment results as reviewer
  persona; follow action_items assigned via topic resolve. Use when simple-waker
  wakes reviewer for pending_review, pending_result_review, or addressed_review_item.
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

准备 `review.yaml`：

```yaml
reasonable_items:
  - "目标清晰"
unreasonable_items:
  - "缺少验收标准"
```

```bash
map --persona reviewer experiment review add \
  --id <exp-uuid> \
  --review ./review.yaml
```

`unreasonable_items` 为空表示无阻塞项；非空时 host 应 `plan revise` 并 `--addressed-item` 回应。

在 host 修订计划并解决 addressed items 后，`pending_reviews` 表示当前 `current_plan_version` 仍缺本 reviewer 的评审记录。若修订已满足要求，提交一个无阻塞项的 review（reasonable_items 写明认可点，unreasonable_items 为空）；若仍有新问题，提交新的 unreasonable_items。不要只 resolve addressed items 后把仍存在的 `pending_reviews` 当成 stale。

## 处理 addressed 项

```bash
map --persona reviewer experiment review list --id <exp-uuid>
map --persona reviewer experiment review resolve-item \
  --id <exp-uuid> \
  --item-id <item-uuid>
```

## 审批实验结果

```bash
map --persona reviewer experiment status --id <exp-uuid>
map --persona reviewer experiment logs --id <exp-uuid>

map --persona reviewer experiment accept-result \
  --id <exp-uuid> \
  --summary "结果通过：验收标准已满足" \
  --file ./result-review.md

map --persona reviewer experiment reject-result \
  --id <exp-uuid> \
  --summary "结果驳回：缺少关键证据" \
  --file ./result-review.md
```

`accept-result` 使实验进入 `done`；`reject-result` 使实验回到 `running`，host 继续返工。不要替 host 修改仓库或直接补执行日志。

## 评审维度（建议）

- 目标与范围是否清晰、可执行
- 验收标准是否可观测（测试、日志、指标）
- 与来源话题共识是否一致
- 风险、依赖、Out of Scope 是否说明
- 是否有遗漏的非目标或安全/权限问题
- 实验结果是否覆盖计划中的 acceptance、测试命令、关键风险和产物路径

## 非目标

- 代替 host 执行实验或改仓库
- 在评审中重写完整 plan（只列条目）
- 假设 bridge 会自动 resolve
- 对自己创建的实验做结果审批

## 参考

- [map-project-collab](../map-project-collab/SKILL.md)
- [experiment-host](../experiment-host/SKILL.md)
