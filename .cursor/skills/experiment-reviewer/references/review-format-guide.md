# 评审格式与维度参考

> 本文档从 [experiment-reviewer SKILL.md](../SKILL.md) 提取的深度参考。当准备提交评审或审批实验结果时阅读本文件。

## review.yaml 格式

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
