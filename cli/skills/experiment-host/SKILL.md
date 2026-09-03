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
| `phase=running` 且 `executor_agent_id == my-id`（自执行） | acquire lock → 改仓库 → 跑验证 → 窄 commit → 写 log（每次 wake 至少推进一个 plan 子项） |
| `phase=running` 且 `executor_agent_id != my-id`（已委派给 participant） | **放手**（informational_only）：participant 通过 `executor_assignments` 接管；host 不要自审、不要写 log、不要代 complete。只剩 `cancel` 权限 |
| `phase=review` 且 `open_unreasonable_count > 0` | `revise_plan` 修订并 `--addressed-item` 回应（[cookbook](references/execution-cookbook.md)） |
| `phase=review` 且 `open_unreasonable_count = 0` | `experiment approve`；`approved` → `experiment start`（可 `--executor participant` 委派执行） |
| `phase=result_review` 且 `actions=[]` | 等 reviewer 审批，不继续执行、不 accept/reject（direct 模式不进 result_review） |
| 全部 acceptance 满足 | 窄 commit → `pre-complete` → `complete` → release lock → 刷新 status/work |

```bash
map --persona host persona whoami
map --persona host work --notification-category wakeable
map --persona host experiment status --id <exp-uuid>
git status --short
```

## 硬性规则

1. 只用 `map --persona host ...` 写 MAP；禁止 MCP 写操作与手写 HTTP
2. **`phase=running` 且 `executor_agent_id == my-id` 表示由你执行**——`executor_agent_id != my-id` 表示已委派给 participant，对 host 是 informational_only，不要写 log、不要代 complete、不要写「等桥接」（participant 在另一条 persona 路径上推进）
3. 一次 wake 完成**当前 phase 的下一步**；自执行时 `running` 每次至少推进**一个 plan 子项**（计划 frontmatter 里的某个编号验收项），写 log 后结束
4. 实验须由本 host persona 创建，否则 approve/start/complete 会 403
5. `complete` 只表示**提交结果待审批**（standard `running -> result_review`；direct `running -> done` 由 [experiment-executor](../experiment-executor/SKILL.md) 推进）；host 禁止自审结果
6. `running` 产生仓库改动时，默认必须提交**窄 git commit**；无法安全区分当前实验改动与其他 dirty worktree 时，停下并在 `experiment log` 记录 blocker，不要继续下一个实验
7. 收尾必须：提交当前实验改动（若有）→ release lock（若已 acquire）→ 刷新 `experiment status` 和 `work`
8. **日志纪律**：create / revise / submit 等任何一次失败后重试成功，都必须补一条 `experiment log` 记录失败原文（422/409 的 error_code 与 hint）与修复动作——踩坑只存在日志里，不依赖会话记忆（log 白名单已放宽到 draft/review/approved 全阶段，阶段拒绝路径已在后续版本删除）

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

## 写入红线（runtime 中立）

**红线条款**（runtime 中立）：禁止用任何文本编辑器或脚本（Edit/Write/sed/python/heredoc 等）直接修改 `map/**` 下任何文件；一切状态变更走 `map` CLI；如需 Read 类工具（cat/head/tail/grep）做诊断允许。

**视为事故触发条件**：发现 audit 链漂移（不论 verify-audit 检测还是 agent 自己注意到，含 server 侧门禁失效导致的非手写场景）→ 停止当前话题状态变更 → 报告 → 等 host/supervisor 决定。

**host 响应**：停止 advance-round / close / 创建实验等状态变更，先调用 `map fs verify-audit` 确认漂移范围并写诊断评论。

## 视图类实验验收 checklist 二段式

> **触发条件**：实验涉及 CLI 命令扩展 / 视图层派生 / 状态机档位调整，且验收点需要 CLI 包入口实际跑通（即 `python -m cli.<module>` 或 `map <cmd>` 子进程，不只是 pytest 直接 import 模块）。

### 段一：synthetic fixture（pytest 跑，CI 自动化）

- 覆盖代码层所有分支的合成 state.json / env / pid 路径
- mock datetime / monkeypatch / freezegun 注入时间戳，避免真 sleep 拖慢 pytest
- 至少 6 case：档位边界 + 缺字段 fallback + pid zombie / defunct + fallback chain 三层 + atomic write race + 同帧一致性
- pytest 全量绿（基线只增不减，0 failed）
- `ruff check` 0

### 段二：真实环境 smoke（监督者手动确认，CLI 包入口 ≠ pytest 直接 import 模块）

- **真实 3-waker 环境**下 host 进长会话 → `map waker status` 显示 busy（live）+ `map work` busy 状态同源对齐（本 checklist 的动机场景）
- 涉及 waker 重启 / 进程级状态变更时由监督者手动重启 server + waker 生效（daemon restart，无 docker build）
- result_review 阶段 action_items 显式收口：监督者重启 server + waker 列入 todo，避免「验收通过却未生效」

### 历史教训（派生公式仅 idle 档同帧 / busy 档漏核）

- 曾有实验只测了 idle 档同帧一致性、漏核 busy 档——真实发生过的验收盲区，事后才派生出修复任务
- 二段式 checklist 的目的：避免「pytest 全绿但 CLI 包入口实际行为漏检」反复发生
- 视图类实验必须在段二显式列出「监督者手动跑哪些 CLI 命令」并写入 plan.md acceptance.evidence_keys

## 参考

| 场景 | 文档 |
|------|------|
| Git 窄 commit / 执行锁细节 / revise_plan / host invoke 参数 | [references/execution-cookbook.md](references/execution-cookbook.md) |
| phase 动作表 / 9 步执行流程 / 收尾模板 / 失败处置 / 错误处理 | [references/lifecycle-transitions.md](references/lifecycle-transitions.md) |
| 通用协作、待办分区语义、瘦身文件引用 | [map-project-collab](../map-project-collab/SKILL.md) |
| 实验创建门禁与话题主持 | [topic-host](../topic-host/SKILL.md) |
