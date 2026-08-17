---
name: experiment-host
description: >-
  Execute MAP experiments as host when resumed by simple-waker: acquire/release
  execution lock, revise plans, implement repo changes with narrow git commits,
  write execution logs via map CLI. Do not rely on deprecated host bridge /
  cli.host_worker. Do not use for: reviewing experiment plans as reviewer,
  hosting topic discussions without experiments, participating as participant.
  Do not accept-result or reject-result on your own experiments (reviewer's job).
  Do not use without first reading map-project-collab Skill. Command cookbook
  (git workflow, lock, revise_plan, host invoke) lives in
  references/execution-cookbook.md; phase action table in
  references/lifecycle-transitions.md.
---

# MAP 实验执行（Host Skill）

在实验 **review / running** 阶段**亲自**用 `map --persona host` CLI 与仓库工具完成工作。**已停用（勿依赖）**：`cli/host_worker` bridge、`start-host-bridge*.sh`、runner 的 stdin/stdout JSON 契约——没有 bridge 会代写 MAP、代跑实验或代提交 git。

被唤醒时先读 [map-project-collab wake.md](../map-project-collab/references/wake.md)（四步协议 + kind 分发表），再回到本 Skill。话题主持见 [topic-host](../topic-host/SKILL.md)。

## 快速判断

| 我看到 | 我该做 |
|--------|--------|
| `phase=running` | acquire lock → 改仓库 → 跑验证 → 窄 commit → 写 log（每次 wake 至少推进一个 plan 子项） |
| `phase=review` 且 `open_unreasonable_count > 0` | `revise_plan` 修订并 `--addressed-item` 回应（[cookbook](references/execution-cookbook.md)） |
| `phase=review` 且 `open_unreasonable_count = 0` | `experiment approve`；`approved` → `experiment start`（可 `--executor participant` 委派执行） |
| `phase=result_review` 且 `actions=[]` | 等 reviewer 审批，不继续执行、不 accept/reject |
| 全部 acceptance 满足 | 窄 commit → `pre-complete` → `complete` → release lock → 刷新 status/work |

```bash
map --persona host persona whoami
map --persona host work --notification-category wakeable
map --persona host experiment status --id <exp-uuid>
git status --short
```

## 硬性规则

1. 只用 `map --persona host ...` 写 MAP；禁止 MCP 写操作与手写 HTTP
2. **`phase=running` 表示由你执行**——不要写「等 host bridge / auto-experiment-lifecycle 接手」
3. 一次 wake 完成**当前 phase 的下一步**；`running` 每次至少推进**一个 plan 子项**（如 I1），写 log 后结束
4. 实验须由本 host persona 创建，否则 approve/start/complete 会 403
5. `complete` 只表示**提交结果待审批**（`running -> result_review`）；host 禁止自审结果
6. `running` 产生仓库改动时，默认必须提交**窄 git commit**；无法安全区分当前实验改动与其他 dirty worktree 时，停下并在 `experiment log` 记录 blocker，不要继续下一个实验
7. 收尾必须：提交当前实验改动（若有）→ release lock（若已 acquire）→ 刷新 `experiment status` 和 `work`
8. **日志纪律（v0.12 M55F，E5 教训）**：create / revise / submit 等任何一次失败后重试成功，都必须补一条 `experiment log` 记录失败原文（422/409 的 error_code 与 hint）与修复动作——踩坑只存在日志里，不依赖会话记忆；同轮日志被状态机拒绝时（如 review 阶段不能写 log），把日志文件落 FS（`map/experiments/<slug>/log-rN.md`）并在进入下一阶段后立即补记

## 执行锁（并发防护）

同一项目同一时刻只允许**一个** `running` 实验持锁；busy 时看 holder 或 `lock skip --next-attempt-at` 退避，崩溃由服务端 TTL 自动释放：

```bash
map --persona host experiment lock acquire --id <exp-uuid>   # 默认 TTL 1800s
map --persona host experiment lock release --id <exp-uuid>
```

## running 收尾（瘦身模式）

`complete` 支持瘦身模式：只存日志文件路径（约定 `map/experiments/<slug>/log.md`），日志内容不进数据库：

```bash
map --persona host experiment complete \
  --id <exp-uuid> \
  --summary "..." \
  --log-file-path map/experiments/<slug>/log.md
```

同理创建实验时可用 `--plan-file-path map/experiments/<slug>/plan.md` 只存计划路径（详见 [map-project-collab file-reference](../map-project-collab/references/file-reference.md)）。完整收尾顺序（pre-complete / evidence metadata / release）见 [lifecycle-transitions.md](references/lifecycle-transitions.md)。

## Host 编排模式（直接调用 reviewer）

不依赖 waker 轮询，host 同步调用其他 persona（如 `submit-review` 后主动通知 reviewer）：

```bash
map --persona host host invoke --persona reviewer \
    --prompt "请评审实验 <exp-uuid> 的计划。先 map --persona reviewer experiment status --id <exp-uuid> 查看上下文，然后提交结构化评审。"
```

参数（`--prompt-file` / `--json` / `--new-session` / `--ignore-waker`）与注意事项见 [cookbook](references/execution-cookbook.md)。调用后仍需 `experiment status` 核实 reviewer 已提交评审。

## 非目标

- 启动或假设 host bridge 在后台运行
- 返回 bridge 用的 JSON（`execution_log_md` 等）而不写 MAP log
- 在 `running` 阶段只 approve/start 不实施
- host 自己调用 `accept-result` 审批自己提交的实验结果

## 常见错误（BAD → GOOD）

| BAD | GOOD |
|-----|------|
| phase=running 写「等 bridge 接手」 | 亲自改仓库并写 log：`experiment log --id <uuid> --summary "I1 完成" --file ./log.md` |
| complete 前不提交 git | 先窄 commit（`map exp <short-id>: <summary>`）再 complete |
| result_review 阶段自审结果 | 等 reviewer；host 不执行 accept-result / reject-result |
| `git add .` 全量提交 | 先 `git status --short` / `git diff --stat` 划边界，只 stage 当前实验文件 |

## 参考

| 场景 | 文档 |
|------|------|
| Git 窄 commit / 执行锁细节 / revise_plan / host invoke 参数 | [references/execution-cookbook.md](references/execution-cookbook.md) |
| phase 动作表 / 9 步执行流程 / 收尾模板 / 失败处置 / 错误处理 | [references/lifecycle-transitions.md](references/lifecycle-transitions.md) |
| 通用协作、待办分区语义、瘦身文件引用 | [map-project-collab](../map-project-collab/SKILL.md) |
| 实验创建门禁与话题主持 | [topic-host](../topic-host/SKILL.md) |
