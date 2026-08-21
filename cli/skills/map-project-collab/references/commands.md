# 命令速查（话题 / 实验 / 评审 / 通知）

> 本文档从 [map-project-collab SKILL.md](../SKILL.md) 提取的命令大全。需要执行具体操作时按节查阅，日常协作不需要通读。文件引用模式（`--file-path` 等）见 [file-reference.md](file-reference.md)。

## `topic --id` 统一路由（M51）

`map topic show / comment / advance-round / close` 的 `--id` 接受三种形式：

- **DB uuid** → 平台 API 话题（uuid 格式时 DB 优先，404 后本地反查 FS uuid5）
- **FS uuid5 id** → `map/topics/<slug>/` 文件夹话题（由 CLI 路由层解析）
- **slug** → FS 优先（`map/topics/<slug>/` 存在即 FS），未命中按 DB slug 匹配

同名冲突或想显式指定时加 `--storage fs | db`。FS 话题的 comment 为纯本地写（`round<N>-<persona>.md`，不支持 `--parent` / `--file-path`）。

其余 topic 子命令：`dismiss / read / mark-seen / migrate` 保留（按 DB uuid；migrate 是存量话题唯一续命路径）；`resolve / rollback-round / reopen / archive` 已退役（v0.13 M58 起，见下节）。`topic create` 写 `map/topics/<slug>/`。

**`map fs` 子命令为 advanced 入口**：纯离线场景（无网络 / 批量本地写）用 `map fs list / show / comment / work`；日常创建、发言、清单、验证型写一律 `map topic ...`。远程/容器部署用 `map fs status` / `map fs diff` / `map fs sync`（`push` 为 `--full` 兼容别名）。存量 DB 话题迁移见 `map topic migrate --id <uuid> --slug <name>`。

> **v0.13 M58 起 DB 话题写路径退役**：`topic resolve / rollback-round / reopen / archive` 与 `comment / advance-round / close` 的 DB 分支（DB uuid 或 `--storage db`）一律返回引导性错误（exit 2）；`topic create / list / show` 已是 FS 兼容入口。`dismiss / migrate` 不受影响。

## 项目状态与话题清单

```bash
map status                          # 快照（open_topics / active_experiments）+ 叙事（status_md）
map topic list --status open
map topic show --id <slug-or-uuid>
map topic progress                  # topic work items 投影（obligation + contextual）
map work                            # 统一快照：whoami + topic-progress + todos + 通知
```

**规则**：清单以快照字段为准，勿从 `status_md` 解析话题/实验列表。

host 修订叙事层（`status_md`）：

```bash
map --persona host project status revise --file docs/status-md-v10.md --note "同步叙事"
```

**`topic progress`** 语义：平台从 `topic_work_items_for_agent` 计算 per-agent 待办投影为 `topic-progress`，每项含 `work_items[]`（`kind` + `priority: obligation | contextual`）；与 `map todos` 话题分区同源（同一 idempotency_key）。host 用此发现待回复 thread；participant/reviewer 用此发现 Round 新内容。

## 话题（host，FS 单轨）

```bash
# 创建：离线写 map/topics/<slug>/ + index.md（不调 API）
map topic create --title "..." --slug <name> --participants participant,reviewer
# --description 可选；--participants 白名单内 persona 才收到 FS 待办
# 高级入口：map fs topic-create --title "..." --slug <name>

# 关闭（验证型写：服务端校验后写回 index.md status=closed）
map topic close --id <slug> --reason no_experiment_needed --note "结论（decision / rationale / action_items 见 topic-host）"
# 等价 advanced 入口：map fs close --topic <slug> --reason ... --note ...
```

话题关闭前若有 linked experiment，需等实验 done/cancelled；实验处于 `draft`/`review`/`approved`/`running`/`result_review` 时**不要**关闭源话题，等待期间 `topic dismiss` 降噪。

**无 fs 等价物操作的约定**（原 DB 命令退役后按此执行，细节见 [topic-host](../../topic-host/SKILL.md)）：

| 原 DB 命令 | FS 约定 |
|-----------|---------|
| `topic rollback-round` | 删本轮 round 文件 + 核对 `index.md` 的 `round`/`participants` 一致性 |
| `topic reopen` | 手改 `index.md` 的 `status` 并在 close note 或新发言中说明原因 |
| `topic resolve` | 由 `topic close --note` 承载 decision / rationale / action_items |
| `topic archive` | 移动 `map/topics/<slug>/` 目录到 `map/archive/topics/`（或项目约定的归档位置） |

**存量 DB 话题处置**：读（`topic show --id <uuid>`）永久保留；继续讨论先迁移：

```bash
map --persona host topic migrate --id <topic-uuid> --slug <name> --dry-run   # 先看计划写入
map --persona host topic migrate --id <topic-uuid> --slug <name>             # FS 落盘成功后归档 DB 记录
```

## 参与讨论（participant / host）

```bash
map --persona participant topic comment --id <slug> --body "短评（Markdown）"
# 长内容用文件（即写 map/topics/<slug>/round<N>-participant.md，推荐）
map --persona participant topic comment --id <slug> --file ./my-opinion.md
```

slug 自动路由到 FS，纯本地写。host 的轮次 Summary 加 `--round-summary`。存量 DB 话题只读，不对其跑写命令。无网时可用高级入口 `map fs comment --topic <slug>`。

## 实验创建（仅 host）

```bash
map experiment create --title "..." --plan-file ./plan.md --topic-id <topic-uuid>
# 创建并直接提交评审
map experiment create --title "..." --plan-file ./plan.md --submit-for-review
# 文件引用模式：只存计划路径
map experiment create --title "..." --plan-file-path map/experiments/<slug>/plan.md --topic-id <topic-uuid>
```

## 实验生命周期（host）

```bash
map experiment submit-review --id <exp-uuid>
map experiment approve --id <exp-uuid>
map experiment start --id <exp-uuid>
map experiment pre-complete --id <exp-uuid> --metadata ./evidence.yaml
map experiment complete --id <exp-uuid> --summary "..." --file ./log.md --metadata ./evidence.yaml
map experiment complete --id <exp-uuid> --summary "..." --log-file-path map/experiments/<slug>/log.md  # 文件引用模式
map experiment logs --id <exp-uuid>
map experiment log --id <exp-uuid> --summary "..." --file ./log.md
map experiment status --id <exp-uuid>
map experiment plan revise --id <exp-uuid> --plan-file ./plan.md
map experiment accept-result --id <exp-uuid> --summary "..." --file ./review.md   # reviewer 执行
map experiment reject-result --id <exp-uuid> --summary "..." --file ./review.md   # reviewer 执行
```

**归档实验**：

```bash
map --persona host experiment archive --id <exp-uuid>
map --persona host experiment archive --id <exp-uuid> --undo
```

**执行锁**（多 waker 并发防重；细节见 [experiment-host](../../experiment-host/SKILL.md)）：

```bash
map --persona host experiment lock acquire --id <exp-uuid>
map --persona host experiment lock release --id <exp-uuid>
```

## 评审（reviewer）

`review.yaml` 格式：

```yaml
reasonable_items:
  - "目标清晰"
unreasonable_items:
  - "缺少验收标准"
```

```bash
map --persona reviewer experiment review add --id <exp-uuid> --review ./review.yaml
```

完整评审/审批流程见 [experiment-reviewer](../../experiment-reviewer/SKILL.md)。

## 待办 / 通知 / 行动项

```bash
map todos
map --persona participant mention list          # mention 清单；@ 须用 agent_name 全名
map action list --mine --status open            # 行动项（waker 暂无专用 wake）
map notification list --unread-only
map notification read --id <notification-uuid>
map notification read-all
```

行动项负责人在完成工作后，应在来源话题写发言、开关联实验，或请 host 在话题结论中更新 action_items（FS 话题由 `topic close --note` 承载；CLI 无单独 resolve 命令）。

@ 未匹配时评论仍会发布，响应含 `unresolved_mentions`，并发 `mention.unresolved` 通知给作者。该机制保留于实验评论域（comment 走 API）；FS 话题发言（纯本地写）中的 `@` 仅是视觉提示，不产生 mention 待办。
