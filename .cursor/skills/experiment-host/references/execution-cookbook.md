# 实验执行手册（Git / 执行锁 / revise_plan / host invoke）

> 从 [experiment-host SKILL.md](../SKILL.md) 下沉的命令细节。执行 running 阶段、修订计划或编排 reviewer 时阅读本文件；phase 判断与生命周期动作表见 [lifecycle-transitions.md](lifecycle-transitions.md)。

## Git 窄 commit 工作流

你是直接改仓库的 Agent。**默认必须把每个实验/子项的已验证改动提交成窄 commit**，避免多个实验的 diff 混在一起；只有用户明确要求「不提交 git」时，才只改文件并在 `experiment log` 中说明。

- 开始前：`git status --short` 和 `git diff --stat`，识别已有 dirty worktree。
- 已有无关改动：不要自动混入提交；只 stage 当前实验明确相关文件。无法区分时停止执行，写 log 说明 blocker。
- 完成一个可验证子项后：先运行验证，再提交 commit，message 使用 `map exp <short-id>: <summary>`。
- 写 `experiment log` 时记录 `commit_sha`、`git_status_after`、改动文件和验证结果。
- `experiment complete` 前：当前实验产生的改动必须已提交；否则不要 complete。
- 回滚：用 commit sha 或 `git log --grep='map exp <short-id>'` 定位。

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

同一项目同一时刻只允许 **一个** `running` 实验持有执行锁。`running` 阶段开始前 acquire，结束后 release：

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

## revise_plan（review 阶段有 open unreasonable 项时）

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

## Host 编排（host invoke）参数

不依赖 waker 轮询，host 同步调用 reviewer / participant agent：

```bash
map --persona host host invoke --persona reviewer \
    --prompt "请评审实验 <exp-uuid> 的计划。先 map --persona reviewer experiment status --id <exp-uuid> 查看上下文，然后提交结构化评审。"
```

| 参数 | 说明 |
|------|------|
| `--prompt` | 完整任务描述（含 experiment_id、需要评审的维度） |
| `--prompt-file` | 从文件读取长 prompt |
| `--json` | 以 JSON 格式输出（含 response + session_id） |
| `--new-session` | 强制开启新 session（默认等待进行中会话结束） |
| `--timeout <秒>` | 等待上限；到点输出**友好报错**（含已等待时长 + 目标 session 状态、无堆栈），并自动向被调方发一条 `host.invoke.cancelled` 的 **wakeable 取消通知**（语义是「告知对方会话已被放弃」，不是强杀进程；被调方 session 仍挂在其 persona 侧，收到通知后自行决定收尾） |
| `--follow` | 流式：阶段性事件（text / tool_use / tool_result）实时打到 **stderr**，stdout 仍只在结束时含最终结果（stdout=数据 / stderr=人类可读） |
| `--ignore-waker` | 在 waker 运行时强制调用（可能冲突） |

**启动状态行**：invoke 启动即向 stderr 输出一行目标 session 状态——`waiting-for-session`（将新建会话）或 `running`（复用既有会话），用于第一次判断「会不会触发 session 冲突」。

**Prompt 首段对象引用约定（A4）**：invoke 的 prompt **第一段固定放对象引用**——topic slug / experiment-id / skill 名，让被调方无需猜测上下文。正文再给任务描述：

```bash
map --persona host host invoke --persona participant \
    --prompt "topic=<topic-slug>; skill=map-project-collab
请参与该话题 Round N 讨论：...（正文）"
```

**适用场景**：`submit-review` 后主动通知 reviewer 评审、`complete` 后主动通知 reviewer 审批结果、需要快速获得 reviewer 反馈而不等待 waker 轮询。

**注意**：调用后仍需通过 `map experiment status` 核实 reviewer 是否已提交评审；reviewer agent 的回复文本在 stdout，但其实际操作（如 `experiment review add`）是通过 `map --persona reviewer` CLI 写入 MAP 平台的。

### 双 invoke 后台并发 + 汇合点模式（A5）

需要 reviewer 与 participant **并行**工作时，把两个 invoke 放后台，然后用**平台对象**做汇合点查询（`map work` / `topic show` / `experiment status`），**不要**在 shell 里拼文件作为唯一事实源：

```bash
# 1) 两个 invoke 后台并行（各自 stderr 进度落文件，便于诊断）
map --persona host host invoke --persona reviewer \
    --prompt "experiment=<exp-uuid>; skill=experiment-reviewer
请评审计划并提交结构化评审。" \
    --follow 2> /tmp/exp-<id>-reviewer.follow.log &
INVOKE_R=$!

map --persona host host invoke --persona participant \
    --prompt "topic=<topic-slug>; skill=map-project-collab
请参与话题讨论发表观点。" \
    --follow 2> /tmp/exp-<id>-participant.follow.log &
INVOKE_P=$!

# 2) 汇合点 = 平台对象，轮询直到两侧真实落库（里程碑），而非等进程退出
until map --persona host experiment review list --id <exp-uuid> | grep -q "已提交评审的标记"; do sleep 10; done
until map --persona host topic show --slug <topic-slug> | grep -q "participant 评论标记"; do sleep 10; done

# 3) 收尾：等两个后台 invoke 结束，检查失败日志
wait $INVOKE_R; wait $INVOKE_P
tail -50 /tmp/exp-<id>-reviewer.follow.log
```

要点：
- **汇合点查询走平台对象**（`map work` / `topic show` / `experiment status` 等），这是 MAP 的事实源；`--follow` 的 stderr 日志只用于诊断「卡在哪一步」，不作为完成判定。
- 任一 invoke 失败（后台 job 非零退出）会静默丢失——补 `wait` 后检查两 job 的退出码。
- 需要强约束时可加各自 `--timeout`，超时方自动收到取消通知，不拖住汇合点。

## 结果审批命令（reviewer / admin 执行，host 禁止自审）

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
