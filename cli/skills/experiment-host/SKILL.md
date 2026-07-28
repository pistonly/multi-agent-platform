---
name: experiment-host
description: >-
  Execute MAP experiments as host when resumed by map-runtime-waker: acquire/release
  execution lock, revise plans, implement repo changes, write execution logs via
  map CLI. Do not rely on deprecated host bridge / cli.host_worker.
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

| phase | 你应执行的动作 |
|-------|----------------|
| `draft` | `map experiment submit-review --id <id>` |
| `review` 且 `open_unreasonable_count > 0` | 修订 plan（见下节 revise_plan） |
| `review` 且 `open_unreasonable_count = 0` | `map experiment approve --id <id>` |
| `approved` | `map experiment start --id <id>` |
| `running` | 按 plan 改代码、跑测试、写 log（见 execute_experiment）；plan 全部验收通过后 `map experiment complete` 提交结果待审批 |
| `result_review` | 等 reviewer `accept-result` 或 `reject-result`；若被驳回回到 `running`，继续返工 |

> **观察项（v0.9 M30A+M31）**：`running` 期间如改动涉及 `inbound_events.rejection_count`（v1 fingerprint 拒绝路径），需在实验日志里附监控口径 —— 单 fingerprint `rejection_count` 增长率、累计 top-N fingerprint、是否需要 `map admin notification cleanup-v1` 兜底（待后续实验定义）。

收到 `my_open_experiments` 待办 wake 时：

```bash
map --persona host experiment status --id <id>
```

若 waker prompt 或 `map work` 中的 `my_open_experiments` 样例带有 `actions`，这就是 host 的实验推进义务：先 `experiment status` 核实，再根据 **phase**、`actions` 与 **open_unreasonable_count** 执行上表对应动作。`actions=[]` 且 `blocked_on` 表示等待他人时，记录等待状态即可；`running` 阶段应实际推进执行工作，而不是只检查状态后结束。

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

0. **（推荐）** `map --persona host experiment lock acquire --id <id>` — 若 lock busy 则 skip 并记录退避时间
1. `map experiment status --id <id>` 阅读 `current_plan`
2. `git status --short` / `git diff --stat` 检查工作树边界；若已有无关 dirty 改动且无法安全拆分，写 blocker 后停止
3. 在仓库内**实际修改**文件；小步、可验证；不要无关重构
4. 运行 plan 中列出的验证命令（pytest、grep 等）
5. 将当前实验改动提交为窄 commit（用户明确禁止提交 git 时除外）
6. 将执行记录写入临时文件，例如 `.map/generated-plans/experiment-<id>-log.md`，内容含：做了什么、改了哪些文件、验证结果、commit_sha、`git_status_after`、风险与后续
7. 写入 MAP：

```bash
map --persona host experiment log \
  --id <exp-uuid> \
  --summary "I1 完成：…" \
  --file ./path/to/log.md
```

8. 若 plan 定义的**全部 acceptance** 已满足，先准备 evidence metadata（例如 `.map/generated-plans/experiment-<id>-evidence.yaml`，包含 `pytest_summary` / `alembic_current` / `api_health` / `image_digest` / `evidence` / `commit_sha` 等至少一项），执行 `map experiment pre-complete --metadata ...`，再调用 `map experiment complete --metadata ...` 提交最终结果日志，实验进入 `result_review`；否则结束本次 wake，等待下次 `experiment_lifecycle` wake 继续下一子项
9. **`experiment lock release --id <id>`**（若步骤 0 已 acquire）

```bash
map --persona host experiment pre-complete \
  --id <exp-uuid> \
  --metadata ./path/to/evidence.yaml

map --persona host experiment complete \
  --id <exp-uuid> \
  --summary "结果提交：…" \
  --file ./path/to/log.md \
  --metadata ./path/to/evidence.yaml
```

### running 收尾模板

```bash
git status --short
git diff --stat
git add <files>
git diff --cached --stat
git commit -m "map exp <short-id>: <summary>"
git rev-parse --short HEAD
git status --short

map --persona host experiment pre-complete \
  --id <exp-uuid> \
  --metadata .map/generated-plans/experiment-<short-id>-evidence.yaml

map --persona host experiment complete \
  --id <exp-uuid> \
  --summary "结果提交：..." \
  --file .map/generated-plans/experiment-<short-id>-result.md \
  --metadata .map/generated-plans/experiment-<short-id>-evidence.yaml

map --persona host experiment lock release --id <exp-uuid>
map --persona host experiment status --id <exp-uuid>
map --persona host work --notification-category wakeable
```

失败时按阶段停下：

- `pre-complete` 失败：修 metadata 或验证证据，不要 complete。
- `complete` 失败：实验仍在 `running`，保留锁或释放前写明 blocker。
- `release` 失败：先查 `experiment status` 的 lock 字段，必要时记录 blocker。
- `status` 显示 `result_review`：host 工作结束，等待 reviewer 审批。
- `git status --short` 显示无关改动：不要混合提交；能明确拆分则只提交当前实验文件，不能拆分则写 blocker 并停止。
- `git commit` 失败：不要 complete；先修复验证、lint 或提交边界问题。

## 常见错误处理

| 现象 | 处理 |
|------|------|
| metadata 文件不存在 / YAML 读失败 | 先创建或修正 evidence 文件，再重跑 `pre-complete`；不要把 traceback 当作 MAP 已写入 |
| `complete` 缺 evidence | metadata 至少包含 `pytest_summary`、`alembic_current`、`api_health`、`image_digest` 或 `evidence`；非部署型才显式 `--allow-missing-evidence` |
| lock busy | 用 `experiment status` 看 holder；必要时 `lock skip --next-attempt-at <ISO8601>` 退避 |
| `403` lifecycle | 核对实验是否由当前 host persona 创建；不要换 MCP/admin 代跑 |
| `result_review` 仍出现在清单 | 这是等待 reviewer 的可见性，不是 host 可执行项 |

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
