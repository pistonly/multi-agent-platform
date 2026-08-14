---
name: experiment-host
description: >-
  Execute MAP experiments as host when resumed by simple-waker: acquire/release
  execution lock, revise plans, implement repo changes, write execution logs via
  map CLI. Do not rely on deprecated host bridge / cli.host_worker.
  Do not use for: reviewing experiment plans as reviewer, hosting topic discussions
  without experiments, participating in discussions as participant. Do not
  accept-result or reject-result on your own experiments (reviewer's job).
  Do not use without first reading map-project-collab Skill.
---

# MAP 实验执行（Host Skill）

本仓库通过 **simple-waker** 唤醒 host Agent session。你在实验 **review / running** 阶段**亲自**用 `map --persona host` CLI 与仓库工具完成工作。

**已停用（勿依赖）**：`cli/host_worker`（host bridge）、`start-host-bridge*.sh` / `start-host-bridge-claude.sh`、runner 的 stdin/stdout JSON 契约。没有 bridge 会代写 MAP、代跑 `execute_experiment` 或代提交 git。

协作入口见 [map-project-collab](../map-project-collab/SKILL.md) § Waker 模式（被唤醒时读其 [references/wake.md](../map-project-collab/references/wake.md) 最小协议）。

## Host 编排模式（直接调用 reviewer）

除 waker 驱动外，host 可以**直接调用** reviewer agent 同步评审实验，无需等待 waker 轮询：

```bash
map --persona host host invoke --persona reviewer \
    --prompt "请评审实验 <exp-uuid> 的计划。先 map --persona reviewer experiment status --id <exp-uuid> 查看上下文，然后提交结构化评审。"
```

- `--prompt` 传完整任务描述（含 experiment_id、需要评审的维度）
- `--prompt-file` 从文件读取长 prompt
- `--json` 以 JSON 格式输出（含 response + session_id）
- `--new-session` 强制开启新 Claude session
- `--ignore-waker` 在 waker 运行时强制调用（可能冲突）

**适用场景**：`submit-review` 后主动通知 reviewer 评审、`complete` 后主动通知 reviewer 审批结果、需要快速获得 reviewer 反馈而不等待 waker 轮询。

**注意**：调用后仍需通过 `map experiment status` 核实 reviewer 是否已提交评审；reviewer agent 的回复文本在 stdout，但其实际操作（如 `experiment review add`）是通过 `map --persona reviewer` CLI 写入 MAP 平台的。

## 硬性规则

1. 先 `map --persona host persona whoami` 与 `map --persona host todos`
2. 只用 `map --persona host ...` 写 MAP；禁止 MCP 写操作与手写 HTTP
3. **`phase=running` 表示由你执行**——不要写「等 host bridge / auto-experiment-lifecycle 接手」
4. 一次 wake 完成**当前 phase 的下一步**；`running` 阶段每次 wake 至少推进 **一个 plan 子项**（如 I1），写 execution log 后结束
5. 实验须由本 host persona 创建，否则 approve/start/complete 会 403
6. `complete` 只表示**提交结果待审批**（`running -> result_review`），不是最终完成；host 禁止自审结果
7. `running` 阶段产生仓库改动时，默认必须提交一个窄 git commit；若无法安全区分当前实验改动与其他 dirty worktree，先停下并在 `experiment log` 记录 blocker，不要继续处理下一个实验

## 快速入口

```bash
map --persona host persona whoami
map --persona host work --notification-category wakeable
map --persona host experiment status --id <exp-uuid>
git status --short
```

判断顺序：

1. `phase=running`：先 acquire lock，再改仓库、跑测试、写 log。
2. `phase=result_review` 且 `actions=[]` / `blocked_on=awaiting_result_approval`：等待 reviewer，不继续执行、不 accept/reject。
3. 收尾必须提交当前实验改动（若有）、release lock（若已 acquire），再刷新 `experiment status` 和 `work`。

## Git

你是直接改仓库的 Agent。**默认必须把每个实验/子项的已验证改动提交成窄 commit**，避免多个实验的 diff 混在一起；只有用户明确要求“不提交 git”时，才只改文件并在 `experiment log` 中说明。

- 开始前：执行 `git status --short` 和 `git diff --stat`，识别已有 dirty worktree。
- 若已有无关改动：不要自动混入提交；只 stage 当前实验明确相关文件。无法区分时停止执行，写 log 说明 blocker。
- 完成一个可验证子项后：先运行验证，再提交 commit，message 使用 `map exp <short-id>: <summary>`。
- 写 `experiment log` 时记录 `commit_sha`、`git_status_after`、改动文件和验证结果。
- `experiment complete` 前：当前实验产生的改动必须已提交；否则不要 complete。
- 回滚：用 commit sha 或 `git log --grep='map exp <short-id>'` 定位。

建议命令：

```bash
git status --short
git diff --stat
# 验证通过后，只 stage 当前实验相关文件
git add <files>
git diff --cached --stat
git commit -m "map exp <short-id>: <summary>"
git rev-parse --short HEAD
git status --short
```

## 执行锁（多 waker / 多 session 并发）

同一项目同一时刻只允许 **一个** `running` 实验持有执行锁。`running` 阶段开始前应 acquire，结束后 release：

```bash
map --persona host experiment lock acquire --id <exp-uuid>   # 默认 TTL 1800s
# ... 改代码、跑测试、写 log ...
map --persona host experiment lock release --id <exp-uuid>
```

| 情况 | 处理 |
|------|------|
| acquire 失败（lock busy） | 另一实验正在执行；用 `experiment status` 看 holder，或 `lock skip --next-attempt-at <ISO8601>` 退避 |
| 进程崩溃未 release | 服务端 TTL 到期后自动释放 |
| 运维强制释放 | `experiment lock force-release --id <uuid> --reason "..."` |

环境变量：`MAP_HOST_NO_LOCK=1` 跳过锁（仅调试）；`MAP_HOST_LOCK_DRY_RUN=1` 只打日志不阻塞。

## my_open_experiments 各阶段

各 phase（draft / review / approved / running / result_review）对应 host 应执行的动作：`running` 前按 plan 推进，`result_review` 等 reviewer 审批。先 `experiment status` 核实，再根据 phase、`actions`、`open_unreasonable_count` 决定下一步。

> **完整 phase 动作表与观察项**：Read [references/lifecycle-transitions.md](references/lifecycle-transitions.md)

## revise_plan

1. `map --persona host experiment review list --id <id>` 查看 open unreasonable 项
2. 在原 plan 基础上修订完整 Markdown（非 diff），保存到例如 `.map/generated-plans/experiment-<id>-revised.md`
3. 提交：

```bash
map --persona host experiment plan revise \
  --id <exp-uuid> \
  --plan-file ./path/to/revised.md \
  --note "回应评审" \
  --addressed-item <item-uuid>    # 每个 open unreasonable 项重复此选项
```

## execute_experiment（running 阶段）

`running` 阶段执行流程：acquire lock → 读 plan → 改仓库 → 跑验证 → 窄 commit → 写 log → 全部 acceptance 满足后 `pre-complete` + `complete` → release lock。每次 wake 至少推进一个 plan 子项。

> **完整 9 步执行流程与 pre-complete/complete 命令**：Read [references/lifecycle-transitions.md](references/lifecycle-transitions.md)

### running 收尾模板

收尾顺序：窄 commit → `pre-complete` → `complete` → release lock → 刷新 status/work。

`complete` 支持瘦身模式：只存日志文件路径（约定 `docs/experiments/<slug>-log.md`），日志内容不进数据库：

```bash
map --persona host experiment complete \
  --id <exp-uuid> \
  --summary "..." \
  --log-file-path docs/experiments/<slug>-log.md
```

同理创建实验时可用 `--plan-file-path docs/experiments/<slug>-plan.md` 只存计划路径（详见 map-project-collab 的 references/file-reference.md）。

> **完整收尾 bash 模板**：Read [references/lifecycle-transitions.md](references/lifecycle-transitions.md)

失败时按阶段停下：`pre-complete`/`complete`/`release`/`git commit` 失败时各有对应处置，`result_review` 表示 host 工作结束。

> **完整失败处置清单**：Read [references/lifecycle-transitions.md](references/lifecycle-transitions.md)

## 常见错误处理

常见问题：metadata 缺失、complete 缺 evidence、lock busy、403 lifecycle、`result_review` 误判为可执行项。

> **完整错误处理表**：Read [references/lifecycle-transitions.md](references/lifecycle-transitions.md)

结果审批命令由 reviewer 或 admin 执行（host 禁止自审）：

```bash
map --persona reviewer experiment accept-result \
  --id <exp-uuid> \
  --summary "结果通过：..." \
  --file ./path/to/review.md

map --persona reviewer experiment reject-result \
  --id <exp-uuid> \
  --summary "结果驳回：..." \
  --file ./path/to/review.md
```

## 非目标

- 启动或假设 host bridge 在后台运行
- 返回 bridge 用的 JSON（`execution_log_md` 等）而不写 MAP log
- 在 `running` 阶段只 approve/start 不实施
- host 自己调用 `accept-result` 审批自己提交的实验结果

## 常见错误（BAD vs GOOD）

### BAD — phase=running 时不执行，写"等 bridge 接手"
> bridge 应该会自动跑实验

### GOOD — 亲自改仓库并写 log
```bash
map --persona host experiment log --id <exp-uuid> --summary "I1 完成" --file ./log.md
```

### BAD — complete 前不提交 git
> 改了文件就行，不用 commit

### GOOD — 先提交窄 commit 再 complete
```bash
git add <files> && git commit -m "map exp <short-id>: <summary>"
map --persona host experiment complete --id <exp-uuid> --summary "..." --file ./log.md
```

### BAD — result_review 阶段自审结果
> 没人审，我先 accept-result

### GOOD — 等 reviewer 审批
```bash
# host 不执行 accept-result / reject-result
map --persona host experiment status --id <exp-uuid>  # 确认 phase=result_review
```

### BAD — 不检查 git status 就提交
> 直接 git add . && git commit

### GOOD — 先检查工作树边界
```bash
git status --short  # 确认只有当前实验的改动
git diff --stat
git add <specific-files>
```

## 参考

- [实验生命周期转换参考](references/lifecycle-transitions.md)
- [topic-host](../topic-host/SKILL.md)
- [map-project-collab](../map-project-collab/SKILL.md)
- [MAP-RUNTIME-WAKER](../../docs/MAP-RUNTIME-WAKER.md)
