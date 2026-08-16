# Round 0 踩坑记录（创建阶段）

## 失败 1：瘦身形态 create 触发 422（旧 server 进程未含 M55 修复）

- 命令：`map --persona host experiment create --title "M56-topic-id-routing" --plan-file-path map/experiments/m56-topic-id-routing/plan.md --submit-for-review`
- 失败原文：

```
Error 422: Plan frontmatter is missing [error_code=STATE_MACHINE_PLAN_MARKER_MISSING]
Hint: Plan must begin with a YAML frontmatter block containing title, acceptance, evidence_keys, dependencies
Escalation: @multi-agent-platform-host (tier=current_caller)
```

- 修复动作：改用全量 `--plan-file` 形态创建成功（本文件同轮记录）。
- 根因分析：CLI 本地预检（M55D）已通过（frontmatter 合规，title/acceptance/evidence_keys/dependencies 齐全），报错来自 8001 server 进程——该进程启动于 M55 提交（aaf3470）之前，仍是旧代码：slim 形态 `PlanInput(file_path=...)` 的 content_md 为空，frontmatter 门禁误报，即 E8 原始 bug 在旧进程上复现。M55 的 server 侧修复需 server 重启后生效。
- 佐证：M55 wire 验证（MAP_DATABASE_URL + DB 副本 + 新代码临时 server）已实证 slim create 201 通过；本次失败仅因线上进程滞后。
- 附带观察（M55E 交付效果）：错误信封的 error_code + Hint + Escalation 三行渲染清晰可执行，正是 M55 交付的行为；若在旧进程上遇到，Hint 文案与真因（进程滞后）不符——错误信封只能描述校验规则，无法诊断部署滞后，属预期边界。
- 处置决定：不动线上 server（用户此前已明确跳过 kill 操作）；本实验用全量形态创建，瘦身形态留待 server 例行重启后自然生效。实验进 running 后按 M55F 纪律补记本条至 experiment log。
