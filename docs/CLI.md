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
```

## 常用流程

```bash
# 创建并进入项目
map bootstrap --key <key> --name "..." --api-url http://localhost:8001

# host 视角：查 open 话题 / 项目状态
map --persona host status
map --persona host topic list --status open
map --persona host topic show --id <uuid>

# host 视角：创建实验（必须由 host 发起；creator_agent_id 必须匹配）
map --persona host experiment create --title "..." --plan-file ./plan.md --topic-id <uuid>

# participant 视角：评论 / 决策
map --persona participant topic comment --id <uuid> --body "..."
map --persona participant topic comment --id <uuid> --file ./comment.md

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

| 命令 | 说明 |
|------|------|
| `map project export` | 导出项目历史到本地 Markdown（话题/实验/决策快照，可提交 Git） |
| `map topic list` | 列出话题（默认排除已归档；`--include-archived` 包含） |
| `map topic show --id <uuid>` | 显示话题详情（含归档对象） |
| `map topic close --id <uuid>` / `topic reopen` | 关闭 / 重开话题 |
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

将项目的话题、实验、决策导出为本地 Markdown 文件，可提交到 Git 让数据跟着项目走。

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
> `.map/history/` 已加入 `.gitignore` 白名单，可以安全提交到 Git。

完整子命令列表请运行 `map --help`。
