# MAP CLI 使用指南（`map`）

MAP 提供一个统一的 Typer CLI：`map`，按子命令分组管理项目、话题、实验、评审、通知等。CLI 内部走官方 Python SDK（`map_client.MAPClient`），无直连 HTTP。

## 安装 & 配置

```bash
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
| `map topic list` | 列出话题（默认排除已归档；`--include-archived` 包含） |
| `map topic show --id <uuid>` | 显示话题详情（含归档对象） |
| `map topic close --id <uuid>` / `topic reopen` | 关闭 / 重开话题 |
| `map experiment list` / `experiment show` | 实验列表 / 详情 |
| `map experiment submit-review / approve / start / complete` | 实验生命周期 |
| `map notification list / read / read-all` | 站内通知 |

完整子命令列表请运行 `map --help`。
