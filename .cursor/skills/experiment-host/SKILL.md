---
name: experiment-host
description: >-
  Execute MAP experiments as host: revise plans from review feedback, implement
  changes in the repo with git checkpoints handled by the bridge, and produce
  execution logs. Use when the host bridge invokes execute_experiment or revise_plan.
---

# MAP 实验执行（Host Skill）

Host bridge 在实验 **review / running** 阶段代你行动。Runner **不**直接调 `map` CLI；bridge 负责 MAP 写入与 **git checkpoint**。

## Git 规则（硬性）

1. **禁止**在 runner 内执行 `git commit` / `git push` — bridge 在改代码前/后会自动提交
2. 修改仓库文件前，bridge 已执行 `checkpoint before experiment <id>`
3. 你完成文件修改后，bridge 会 `git add -A && git commit` 记录实验产出
4. 回滚：使用 `git log --grep='map: checkpoint before experiment'` 或 state 中的 `git_checkpoint_before` SHA

## revise_plan

- 阅读 `unreasonable_items`，在原计划基础上增补/澄清
- 输出完整修订后 plan Markdown（非 diff）
- 保持可执行步骤与可观测验收标准

## execute_experiment

- 按 `current_plan` 在仓库内**实际修改**必要文件（文档、脚本、配置等）
- 尽量小步、可验证；不要无关重构
- 完成后在 JSON 中返回 `execution_log_md`（含：做了什么、改了哪些文件、如何验证、风险与后续）
- 若计划无需改代码，在 log 中说明并列出验证命令输出

## 输出 JSON（execute_experiment）

```json
{
  "summary": "一句话结论",
  "execution_log_md": "# 执行日志\n..."
}
```

## 输出 JSON（revise_plan）

```json
{
  "body": "完整 plan Markdown",
  "change_note": "修订说明"
}
```

## 参考

- [topic-host](../topic-host/SKILL.md)
- [map-project-collab](../map-project-collab/SKILL.md)
- [MAP-AGENT-RUNTIME](../../docs/MAP-AGENT-RUNTIME.md)
