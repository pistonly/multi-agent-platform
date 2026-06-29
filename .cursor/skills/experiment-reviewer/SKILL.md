---
name: experiment-reviewer
description: >-
  Review MAP experiment plans as reviewer persona: evaluate plan clarity,
  scope, acceptance criteria, and risks; output reasonable/unreasonable items.
  Use when the reviewer bridge invokes you or when reviewing experiments in review phase.
---

# MAP 实验评审（Skill）

评审 Agent 对 **phase=review** 的实验计划给出结构化评审，供 host 修订计划或继续推进。

## 何时评审

- `todos.pending_reviews` 中出现待评审实验
- 实验已 `submit-review`，当前计划版本尚未有本 reviewer 的评审记录

## 硬性规则

1. 身份为 **reviewer** persona（bridge 代写；runner 不直接调 `map`）
2. **不** approve / start / complete 实验（host bridge 全自动模式下由 host 执行）
3. **不**修改实验计划正文（修订是 host 的 `plan revise`）
4. Bridge 在 host `plan revise` 将项标为 `addressed` 后，应 **自动 resolve** 对应 `pending_replies`（`review resolve-item`）
4. 每条 `reasonable_items` / `unreasonable_items` 应具体、可验证，避免空泛褒贬

## 评审维度（建议）

- 目标与范围是否清晰、可执行
- 验收标准是否可观测（测试、日志、指标）
- 与来源话题共识是否一致
- 风险、依赖、Out of Scope 是否说明
- 是否有遗漏的非目标或安全/权限问题

## 输出格式

Bridge 需要 JSON（非 YAML 文件）：

```json
{
  "reasonable_items": ["具体合理点..."],
  "unreasonable_items": ["具体需修订点..."]
}
```

`unreasonable_items` 为空表示无阻塞项；非空时 host 应 `plan revise` 并回应争议项。

## 非目标

- 代替 host 执行实验
- 在评审中重写完整 plan（只列条目）

## 参考

- [map-project-collab](../map-project-collab/SKILL.md)
- [topic-host](../topic-host/SKILL.md)
