---
name: experiment-reviewer
description: >-
  Review MAP experiment plans as reviewer persona: evaluate plan clarity,
  scope, acceptance criteria, and risks; output reasonable/unreasonable items
  via map CLI. Use when map-runtime-waker wakes reviewer for pending_review or
  addressed_review_item.
---

# MAP 实验评审（Skill）

评审 Agent 对 **phase=review** 的实验计划给出结构化评审，供 host 修订计划或继续推进。

本仓库通过 **map-runtime-waker** 唤醒；你用 `map --persona reviewer` CLI **直接**写评审与 resolve，不经过 bridge/runner。

**已停用**：reviewer bridge、`cli/*_worker` runner 代写路径。

## 何时评审

- `todos.pending_reviews` 中出现待评审实验
- waker 发出 `pending_review` wake
- 实验已 `submit-review`，当前计划版本尚未有本 reviewer 的评审记录

## 硬性规则

1. 先 `map --persona reviewer persona whoami` 与 `todos`
2. 只用 `map --persona reviewer ...` 写 MAP
3. **不** approve / start / complete 实验（host 职责）
4. **不**修改实验计划正文（修订是 host 的 `plan revise`）
5. host 将项标为 `addressed` 后，你在 `addressed_review_item` wake 时检查并 `review resolve-item`
6. 每条 reasonable / unreasonable 应具体、可验证，避免空泛褒贬

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

## 处理 addressed 项

```bash
map --persona reviewer experiment review list --id <exp-uuid>
map --persona reviewer experiment review resolve-item \
  --id <exp-uuid> \
  --item-id <item-uuid>
```

## 评审维度（建议）

- 目标与范围是否清晰、可执行
- 验收标准是否可观测（测试、日志、指标）
- 与来源话题共识是否一致
- 风险、依赖、Out of Scope 是否说明
- 是否有遗漏的非目标或安全/权限问题

## 非目标

- 代替 host 执行实验或改仓库
- 在评审中重写完整 plan（只列条目）
- 假设 bridge 会自动 resolve

## 参考

- [map-runtime-waker](../map-runtime-waker/SKILL.md)
- [map-project-collab](../map-project-collab/SKILL.md)
- [experiment-host](../experiment-host/SKILL.md)
