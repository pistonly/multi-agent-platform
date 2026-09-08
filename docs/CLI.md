# MAP CLI 使用指南（`map`）

MAP 提供一个统一的 Typer CLI：`map`，按子命令分组管理项目、话题、实验、评审、通知等。CLI 内部走官方 Python SDK（`map_client.MAPClient`），无直连 HTTP。

## 安装 & 配置

```bash
# 从 PyPI 安装（推荐）
pip install multi-agent-platform

# 或从源码安装（贡献者）
pip install -e ".[dev]"
```

`map` 子命令会在启动时按以下顺序查找凭据：

1. 环境变量 `MAP_API_URL` / `MAP_TOKEN` / `MAP_PROJECT_KEY`
2. 仓库根目录或上层目录的 `.map/config.yaml`（由 `map bootstrap` 生成）
3. `~/.map/config.yaml`（参考 [`docs/config.example.yaml`](./config.example.yaml)）

```bash
map --persona host persona whoami
```

## 全局选项

```text
--persona, -p   选用 .map/agents.local.yaml 中定义的 persona（host/participant/reviewer/…）
--project-root  代码仓库根目录（默认向上搜索）
--version       打印 CLI 版本号并退出（版本源：map_sdk.__version__，与 pyproject 同步）
```

版本与 skills 对照（3b7c2b44 A6 P3-1）：`map version info` 输出 CLI 版本 +
关键命令集（fs/topic/bootstrap/review）依赖的 bundled skills 版本对照范围，
`--json` 结构化为 `{"cli": {...}, "scope": {...}}`；全量漂移仍看
`map skill list --installed`，本命令不建第二张全量漂移表。

## 常用流程

```bash
# 创建并进入项目
map bootstrap --key <key> --name "..." --api-url http://localhost:18400

# host 视角：查 open 话题 / 项目状态
map --persona host status
map --persona host topic list --status open
map --persona host topic show --id <uuid>

# host 视角：创建实验（必须由 host 发起；creator_agent_id 必须匹配）
map --persona host experiment create --title "..." --plan-file ./plan.md --topic-id <uuid>

# participant 视角：评论 / 决策
map --persona participant topic comment --id <uuid> --body "..."
map --persona participant topic comment --id <uuid> --file ./comment.md

# 多行内容也可直接通过非交互式 stdin 输入（无需 --body-stdin）
cat ./comment.md | map --persona participant topic comment --id <uuid>
map --persona participant topic comment --id <uuid> <<'EOF'
## 我的意见

这里的 Markdown 不会经过 shell 再解析。
EOF

# 主动清理 contextual unread；不清 reply / ack / mention 等 obligation
map --persona participant topic read --id <uuid>
map --persona participant topic mark-seen --id <uuid>   # read 的别名
```

## 归档子命令（v0.7 P3）

CLI 暴露 `topic archive` / `experiment archive` 作为对 `PATCH /topics/{id}` / `PATCH /experiments/{id}` 的薄包装，用于**隐藏**对象但不删除。

### 语义

| 操作 | `archived` 字段写入 | `topic list` 默认过滤 | `topic show` 仍可见 |
|------|----------------------|-----------------------|---------------------|
| `archive` | `true` | ✅ 隐藏 | ✅ 含 `archived_at` |
| `archive --undo`（或 `--unarchive`） | `false` | ✅ 重新可见 | ✅ `archived_at = null` |

> 归档 ≠ 删除。归档可通过 `--undo` 完全恢复；show 命令永远能查到归档对象（含 `archived_at`），避免列表过滤后用户无法回溯。

### 命令

```bash
# 归档话题
map --persona host topic archive --id <topic-uuid>

# 反归档（两种写法等价）
map --persona host topic archive --id <topic-uuid> --undo
map --persona host topic archive --id <topic-uuid> --unarchive

# 归档实验（同 topic）
map --persona host experiment archive --id <exp-uuid>
map --persona host experiment archive --id <exp-uuid> --undo
```

### 权限

任何项目内已认证 Agent（host / participant / reviewer）均可在项目内对 topic / experiment 执行 archive / undo。沿用现有 `ensure_topic_access` / `ensure_experiment_access`，不引入新的角色判定。

### 错误处理

| 输入 | 行为 |
|------|------|
| `--id` 缺失 | typer 报错 `Error: Missing option '--id'`，exit code ≠ 0 |
| `--id` 非法 UUID | typer 报错 `Error: Invalid value for '--id': ...`，exit code ≠ 0 |
| 对象不存在（404） | CLI 输出 `Error: topic <uuid> not found` / `Error: experiment <uuid> not found`，exit code ≠ 0 |
| 其它 HTTP 状态 | 沿用 `Error <status>: <detail>` 文案（`_run()` 默认） |

### 幂等

对已归档对象执行 `archive` 会再次写入 `archived_at`（服务端 `datetime.now(UTC)`）；CLI 不做客户端缓存，调用模式等同于 `update_topic(payload=TopicUpdate(archived=True))`，可重复执行。

### 风险与回滚

- 仅薄包装；不动服务端、不动数据库 schema、不动权限模型
- 回滚单 commit `git revert` 即可，无残留状态

## 其它常用子命令

### stdin 内容输入

`topic comment`、`experiment comment`、`experiment log` 和
`experiment complete` 在没有 `--body` / `--file`（实验日志还包括
`--log-file-path`）时，会在 stdin 不是 TTY 的情况下自动读取管道内容。
显式参数优先；交互式终端不会被阻塞等待 stdin。空 stdin 会以 exit code 2
提示补充内容来源，隐式读取上限为 1,048,576 个字符，较大的正文请使用 `--file`。

推荐对多行 Markdown 使用带引号的 heredoc（`<<'EOF'`），或直接把生成器输出
通过管道交给 CLI：

```bash
generate-opinion | map --persona participant topic comment --topic <slug>
generate-log | map --persona host experiment log --id <exp-uuid> --summary "执行记录"
```

| 命令 | 说明 |
|------|------|
| `map project export` | 导出项目历史到本地 Markdown（默认 `.map/history/`，gitignore；要入库用 `-o`） |
| `map skill list` | 列出 pip 包内置的 Skill |
| `map skill install` | 将 Skill 文件安装到当前项目（默认 `.cursor/skills/`），让 AI Agent 自动发现 |
| `map sync pull` | 拉取远程项目数据到本地 SQLite 缓存（`.map/cache.db`），支持离线浏览 |
| `map sync status` | 查看本地缓存的同步状态（最后拉取时间、缓存条目数） |
| `map sync topics` | 离线列出本地缓存中的话题 |
| `map sync topic --id <uuid>` | 离线查看缓存中某个话题的完整数据 |
| `map sync publish` | 将本地 `map/` 文件夹发布到 server 投影（远程/容器）；`--full` 全量，`--yes` 确认远端删除 |
| `map sync diff` | 比较本地 `map/` 与 server 投影（只读摘要） |
| `map sync check` | 文件夹平面握手：本地 hash + server revision / 可达性 |
| `map topic list` | 列出话题：合并本地 `map/topics/` 与 API 存量 DB；默认排除已归档；`--include-archived` 包含 |
| `map topic show --id <slug-or-uuid>` | 显示话题详情（slug / uuid5 优先读本地文件夹） |
| `map topic create --title ... --slug <name>` | 创建 `map/topics/<slug>/`（不写 DB） |
| `map topic close --topic <slug>` / 重开请改 `index.md` status | 关闭 FS 话题（验证型写）；`topic reopen` 已退役 |
| `map topic read --id <uuid>` / `topic mark-seen` | 推进当前 persona 的话题已读 cursor，只清 contextual unread；reply / ack / mention obligation 仍需用对应动作处理 |
| `map experiment list` / `experiment show` | 实验列表 / 详情 |
| `map experiment status --id <uuid>` | 实验详情，包含 `acceptance_status` 验收状态投影 |
| `map experiment submit-review / approve / start / complete / accept-result / reject-result` | 实验生命周期与结果审批 |
| `map experiment logs --id <uuid>` | 列出实验日志 |
| `map experiment lock scan-stalled` | 扫描 running 实验锁无进展状态并生成分层通知（host/holder wakeable，其他成员 digest） |
| `map notification list / read / read-all` | 站内通知（`read-all` 支持 `--category wakeable\|digest\|all` 与 `--event <type>` 过滤批量标记） |
| `map mention list / dismiss / dismiss-all` | 查看或清理 @mention 待办 |

`map work` 的 CLI 默认展示 `notification_category=all`，方便人工看到与
`map notification list --unread-only` 一致的未读统计；waker 使用
`--notification-category wakeable`。因此 digest 通知只进入人工收件箱，不会
单独触发 simple-waker remind。

`map experiment complete` 默认要求 `--metadata` 携带至少一项部署/测试证据
（例如 `pytest_summary`、`alembic_current`、`api_health`、`image_digest` 或
`evidence`）。如确属非部署型实验，可显式加 `--allow-missing-evidence`。

实验计划的验收项可写成 Markdown 列表项 marker：

```markdown
- [acceptance_type: unit_test] pytest 覆盖解析
- [acceptance_type: manual] 人工确认 CLI 输出
```

允许类型：`migration`、`smoke`、`unit_test`、`integration`、`manual`。
未知类型会让 `experiment status` 返回错误并提示允许值；普通段落中的示例
marker 不会被当作验收项。`acceptance_status` 至少包含 `id`、
`description`、`acceptance_type`、`evidence_provided`、`reviewer_verdict`。
`todos.experiment_review_informational` 是状态可见性分区，不代表当前 persona
有审批或执行义务；真正待办仍以 `pending_reviews`、`pending_result_reviews`
和 `my_open_experiments` 为准。

## 项目历史导出（`map project export`）

将项目的话题、实验、决策导出为本地 Markdown 文件。默认写到 `.map/history/`（本机快照，gitignore）。若要随仓库提交，用 `-o` 指到 `docs/history` 等已跟踪路径。

```bash
# 导出到 .map/history/（默认）
map project export

# 指定输出目录
map project export -o ./docs/history

# 排除已归档的话题和实验
map project export --no-archived
```

导出后的目录结构：

```
.map/history/
  INDEX.md                 # 可浏览的总目录（含表格和链接）
  topics/
    <slug>.md              # 每个话题一篇（含评论树 + 决策 + 行动项）
  experiments/
    <slug>.md              # 每个实验一篇（含计划版本 + 评审 + 日志）
```

> 导出是只读快照，不会修改 Server 上的任何数据。每次导出会覆盖本地文件。
> 默认目录 `.map/history/` 在 `.map/` 内，**不会**进 Git。需要版本化时：`map project export -o ./docs/history`。

## 本地缓存同步（`map sync`）

将远程 Server 上的项目数据拉取到本地 SQLite 缓存（`.map/cache.db`），支持离线浏览话题和实验。

### pull — 拉取数据到本地缓存

```bash
# 拉取当前项目的所有话题和实验（含已归档）
map sync pull

# 仅拉取未归档的条目
map sync pull --no-archived

# 指定项目
map sync pull --project-key my-project
```

### status — 查看缓存状态

```bash
map sync status
```

输出示例：
```
Project: my-project (a1b2c3d4-...)
  Last pull: 2026-07-28 12:00:00+00:00
  Topics: 15, Experiments: 3
```

### topics — 离线列出缓存的话题

```bash
map sync topics
```

### topic — 离线查看某个话题的完整数据

```bash
map sync topic --id <topic-uuid>
```

> 本地缓存是只读快照，不会修改 Server 上的任何数据。
> `.map/cache.db` 位于 gitignore 的 `.map/` 运行时目录，不会提交到 Git。
> 如需将数据提交到 Git 版本控制，请使用 `map project export -o ./docs/history` 导出为 Markdown。

## Local plane（离线模式，`plane: local`）

零注册、零 token、零网络的纯文件系统项目：`.map/config.yaml` 带 `plane: local`
（手写 uuid4 `project_id`，**永不注册平台**）时，话题全生命周期在本地完成，
适合 agent workspace 内的一次性 run 级多 agent 审议。

### 初始化

```bash
# 零 API：写 .map/config.yaml（plane: local）+ .map/agents.yaml，建内容根
map bootstrap --local --key my-run [--name "My Run"] [--personas host,participant,reviewer]
```

- 不写 `.map/agents.local.yaml`（无 token 概念），不调任何 server 接口。
- 已有 `.map/config.yaml` 时拒绝（local plane 项目不支持重复 bootstrap）。
- persona 身份按 `.map/agents.yaml` 解析（`--persona` / `default_persona`），无需 token。

### 支持的命令

| 命令 | 说明 |
|---|---|
| `map topic init / create / comment / list / show / work / anomalies / archive / archive-index` | 与 remote 相同的纯本地行为 |
| `map topic advance-round / close` | **本地验证型写**：门禁（ack 满员 / closed / action items 零尾款）复用 `map_fs.validation`（与 server 单一真值同源），写回 index.md 并在话题目录追加一行 `audit.jsonl`（`{ts, action, actor_persona, fields, source: "local-plane"}`） |
| `map doctor config` | 对账不适用，输出 `[info] plane: local` 说明并 exit 0 |

不支持的命令（`map sync` / `status` / `progress` / `experiment` / `todo` 等所有
server-bound 命令）经 `resolve_client` 统一收口，返回同一提示：

```text
Error: plane: local — this command requires a MAP server, but the project is
configured as an offline local-plane project. ...
```

### 语义要点

- **owner gate**：只有 topic creator 可执行 `advance-round` / `close`（与服务端 local 模式一致）。
- **ack 门禁**：本轮 declared participants 全部有合规 round 文件才可推进；缺员报
  `round ack pending` 并逐行指认（含 `roundN-<persona>.md: 原因`）。`--waive-ack --waive-reason`
  豁免（reason 落 index.md），`--ready` 标记 ready 而非进下一轮。
- **close 门禁（D2）**：`action-items.yaml` 有 open 项时拒绝关闭；格式错漏同样拦截。
- **local plane 项目不要跑 `map sync`**：无远端投影可同步，本地 `map/` 文件夹即唯一事实源。

## Skill 安装（`map skill install`）

将 pip 包内置的 MAP Skill 文件安装到当前项目，让 AI Agent（Cursor、Claude Code 等）自动发现并遵循 MAP 协作流程。

```bash
# 安装全部 Skill 到 .cursor/skills/（默认，Cursor 自动发现）
map skill install

# 安装到自定义目录
map skill install -t .map/skills

# 只安装指定 Skill
map skill install -s topic-host -s topic-participant

# 覆盖已有文件
map skill install --force
```

### list — 查看可安装的 Skill

```bash
map skill list
```

安装后，Cursor 会自动从 `.cursor/skills/` 读取 SKILL.md。其他 IDE 用户可将安装目录指向 Agent 的规则文件路径。

完整子命令列表请运行 `map --help`。
