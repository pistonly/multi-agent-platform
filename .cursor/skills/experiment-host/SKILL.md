---
name: experiment-host
description: >-
  Execute MAP experiments as host when resumed by map-runtime-waker: revise plans,
  implement repo changes, write execution logs via map CLI. Do not rely on
  deprecated host bridge / cli.host_worker.
---

# MAP 实验执行（Host Skill）

本仓库通过 **map-runtime-waker** 唤醒 host Agent session。你在实验 **review / running** 阶段**亲自**用 `map --persona host` CLI 与仓库工具完成工作。

**已停用（勿依赖）**：`cli/host_worker`（host bridge）、`start-host-bridge*.sh` / `start-host-bridge-claude.sh`、runner 的 stdin/stdout JSON 契约。没有 bridge 会代写 MAP、代跑 `execute_experiment` 或代提交 git。

协作入口见 [map-runtime-waker](../map-runtime-waker/SKILL.md)。

## 硬性规则

1. 先 `map --persona host persona whoami` 与 `map --persona host todos`
2. 只用 `map --persona host ...` 写 MAP；禁止 MCP 写操作与手写 HTTP
3. **`phase=running` 表示由你执行**——不要写「等 host bridge / auto-experiment-lifecycle 接手」
4. 一次 wake 完成**当前 phase 的下一步**；`running` 阶段每次 wake 至少推进 **一个 plan 子项**（如 I1），写 execution log 后结束
5. 实验须由本 host persona 创建，否则 approve/start/complete 会 403
6. `complete` 只表示**提交结果待审批**（`running -> result_review`），不是最终完成；host 禁止自审结果

## Git

你是直接改仓库的 Agent（用户未禁止时可自行 commit）：

- 大项开始前：记录回滚点，例如 `git commit --allow-empty -m "map: checkpoint before experiment <id>"` 或记下当前 HEAD
- 完成子项后：提交有意义的 commit message
- 回滚：`git log --grep='map: checkpoint before experiment'`

## experiment_lifecycle 各阶段

| phase | 你应执行的动作 |
|-------|----------------|
| `draft` | `map experiment submit-review --id <id>` |
| `review` 且 `open_unreasonable_count > 0` | 修订 plan（见下节 revise_plan） |
| `review` 且 `open_unreasonable_count = 0` | `map experiment approve --id <id>` |
| `approved` | `map experiment start --id <id>` |
| `running` | 按 plan 改代码、跑测试、写 log（见 execute_experiment）；plan 全部验收通过后 `map experiment complete` 提交结果待审批 |
| `result_review` | 等 reviewer `accept-result` 或 `reject-result`；若被驳回回到 `running`，继续返工 |

收到 `experiment_lifecycle` wake 时：

```bash
map --persona host experiment status --id <id>
```

根据 **phase** 与 **open_unreasonable_count** 执行上表对应动作。**禁止**在 `running` 仅调用 `start` 或只检查状态后结束。

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

1. `map experiment status --id <id>` 阅读 `current_plan`
2. 在仓库内**实际修改**文件；小步、可验证；不要无关重构
3. 运行 plan 中列出的验证命令（pytest、grep 等）
4. 将执行记录写入临时文件，例如 `.map/generated-plans/experiment-<id>-log.md`，内容含：做了什么、改了哪些文件、验证结果、风险与后续
5. 写入 MAP：

```bash
map --persona host experiment log \
  --id <exp-uuid> \
  --summary "I1 完成：…" \
  --file ./path/to/log.md
```

6. 若 plan 定义的**全部 acceptance** 已满足，调用 `map experiment complete` 提交最终结果日志，实验进入 `result_review`；否则结束本次 wake，等待下次 `experiment_lifecycle` wake 继续下一子项

结果审批命令由 reviewer 或 admin 执行：

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

## 参考

- [map-runtime-waker](../map-runtime-waker/SKILL.md)
- [topic-host](../topic-host/SKILL.md)
- [map-project-collab](../map-project-collab/SKILL.md)
- [MAP-RUNTIME-WAKER](../../docs/MAP-RUNTIME-WAKER.md)
