# 命令速查（话题 / 实验 / 评审 / 通知）

> 本文档从 [map-project-collab SKILL.md](../SKILL.md) 提取的命令大全。需要执行具体操作时按节查阅，日常协作不需要通读。文件引用模式（`--file-path` 等）见 [file-reference.md](file-reference.md)。

## `topic --id` 统一路由（M51）

`map topic show` 的 `--id` 接受三种形式：

- **DB uuid** → 平台 API 话题（uuid 格式时 DB 优先，404 后本地反查 FS uuid5）
- **FS uuid5 id** → `map/topics/<slug>/` 文件夹话题（由 CLI 路由层解析）
- **slug** → FS 优先（`map/topics/<slug>/` 存在即 FS），未命中按 DB slug 匹配

同名冲突或想显式指定时加 `--storage fs | db`。

**写操作发现入口是 `map topic`**（与看板可复制命令一致）：`create` / `comment` / `advance-round` / `close` / `archive`。`--topic` 与 `--id` 双轨别名均可用。

其余 topic 子命令：`list / show / progress / history / dismiss / read / mark-seen / migrate / action-item` 出现在 `map topic --help`；`resolve / rollback-round / reopen` 已退役（v0.13 M58 起，help 中隐藏，调用仍给引导）。远程/容器部署用 `map sync check` / `map sync diff` / `map sync publish`（`push` 为 `--full` 兼容别名）。存量 DB 话题迁移见 `map topic migrate --id <uuid> --slug <name>`；批量迁移（DB → FS projection）用 `map sync migrate scan / dry-run / execute / verify`（`status` 看 project 级汇总；execute 逐 item 提交、幂等、可中断续跑）。

> **v0.13 M58 起 DB 话题写路径退役**：`topic resolve / rollback-round / reopen` 与 `comment / advance-round / close` 的 DB 分支（DB uuid 或 `--storage db`）一律返回引导性错误（exit 2）。归档走 `map topic archive`（移动文件夹）。`dismiss / migrate` 不受影响。

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
map --persona host project status revise --file docs/status-md-v11.md --note "同步叙事"
```

**`topic progress`** 语义：平台从 `topic_work_items_for_agent` 计算 per-agent 待办投影为 `topic-progress`，每项含 `work_items[]`（`kind` + `priority: obligation | contextual`）；与 `map todos` 话题分区同源（同一 idempotency_key）。host 用此发现待回复 thread；participant/reviewer 用此发现 Round 新内容。

## 话题（host，FS 单轨）

```bash
# 创建：离线写 map/topics/<slug>/ + index.md（不调 API）
map topic create --title "..." --slug <name> --participants participant,reviewer
# --description 可选；--participants 白名单内 persona 才收到 FS 待办

# 轻量执行项（收敛时落 action-items.yaml，close 门禁校验清零，见 topic-host §3b）：
map topic action-item add --topic <slug> --owner <persona> --title "..."
map topic action-item complete --topic <slug> --id <n> --evidence "<commit/pytest/路径>"
map topic action-item cancel --topic <slug> --id <n> --reason "..."

# 关闭（验证型写：服务端校验 ack 与执行项清零后写回 index.md status=closed）
# --reason 只接受 4 值：experiment_ready / experiment_done / cancelled / discussion_converged
map topic close --topic <slug> --reason discussion_converged --note $'结论（decision / rationale 见 topic-host；执行项在 action-items.yaml）\nexperiment_id: none\nfollowup_gate: <闭环追踪描述>'
```

话题关闭前若有 linked experiment，需等实验 done/cancelled；实验处于 `draft`/`review`/`approved`/`running`/`result_review` 时**不要**关闭源话题，等待期间 `topic dismiss` 降噪。

**close `--reason` 枚举**（与 `map_fs.validation.CLOSE_REASON_LEGAL` 同源；非法值在写入时直接拒绝，报 `InvalidCloseReasonError`）：

| reason | 语义 |
|--------|------|
| `experiment_ready` | 收敛后开实验，等实验就绪 |
| `experiment_done` | 收敛后开实验，实验已 done/cancelled |
| `cancelled` | 话题主动取消（不开实验） |
| `discussion_converged` | 讨论收敛但不开实验（仅沉淀决策 / 不挂实验链路） |

**`discussion_converged` 的 note 结构化字段**（平台强制校验，缺字段 → `InvalidCloseNoteError`）：首行为自由文本，其后按 `key: value` 行给出——

| 字段 | 必填 | 说明 |
|------|------|------|
| `experiment_id` | 是 | 关联实验 uuid；不开实验写 `none` |
| `followup_gate` | 是 | 闭环追踪描述（必须非空） |
| `drift_ack` | 可选 | 已知漂移的确认说明 |

其余 reason 不校验 note 字段。

**无 fs 等价物操作的约定**（原 DB 命令退役后按此执行，细节见 [topic-host](../../topic-host/SKILL.md)）：

| 原 DB 命令 | FS 约定 |
|-----------|---------|
| `topic rollback-round` | 删本轮 round 文件 + 核对 `index.md` 的 `round`/`participants` 一致性 |
| `topic reopen` | 手改 `index.md` 的 `status` 并在 close note 或新发言中说明原因 |
| `topic resolve` | 由 `topic close --note` 承载 decision / rationale；轻量执行项走 `action-items.yaml`（`map topic action-item ...`，close 门禁校验清零） |
| `topic archive` | 移动 `map/topics/<slug>/` 目录到 `map/archive/topics/`（或项目约定的归档位置） |

**存量 DB 话题处置**：读（`topic show --id <uuid>`）永久保留；继续讨论先迁移：

```bash
map --persona host topic migrate --id <topic-uuid> --slug <name> --dry-run   # 先看计划写入
map --persona host topic migrate --id <topic-uuid> --slug <name>             # FS 落盘成功后归档 DB 记录
```

**读路径退役 flag（`topic_db_read_retired`）**：host/admin 可用
`map project config flag set --key topic_db_read_retired --value on --reason "<审计理由>"` 退役内容侧 DB 读。开启后：话题列表只出 FS 话题；未迁移存量话题的 show 评论读取与 archive 返回 **410**（错误码 `topic_db_read_retired`），并引导 `map topic migrate` / `map fs archive`。**顺序契约：先把存量迁移清零，再翻 flag**（翻 on 必须带非空 reason；回滚 `--value off`，off 可不带 reason）。迁移进度用 `map sync migrate status` 核对。

## 参与讨论（participant / host）

```bash
map --persona participant topic comment --topic <slug> --body "短评（Markdown）"
# 长内容用文件（即写 map/topics/<slug>/round<N>-participant.md，推荐）
map --persona participant topic comment --topic <slug> --file ./my-opinion.md
# 也可省略 --body/--file，直接从非交互式 stdin 读取多行 Markdown
cat ./my-opinion.md | map --persona participant topic comment --topic <slug>
```

slug 自动路由到 FS，纯本地写。host 的轮次 Summary 加 `--round-summary`，CLI 会写入独立的 `round<N>-summary-host.md`，无需 `--force` 覆盖原发言。存量 DB 话题只读，不对其跑写命令。`map topic comment --id <slug>` 与 `--topic` 等价。

## 实验创建（仅 host）

```bash
# --topic-id 接受 DB uuid 或 FS 话题 slug（slug → 确定性 uuid5）
map experiment create --title "..." --plan-file ./plan.md --topic-id <topic-ref>
# 创建并直接提交评审
map experiment create --title "..." --plan-file ./plan.md --submit-for-review
# 文件引用模式：只存计划路径
map experiment create --title "..." --plan-file-path map/experiments/<slug>/plan.md --topic-id <topic-ref>
```

## 实验生命周期（host）

```bash
map experiment submit-review --id <exp-uuid>
map experiment approve --id <exp-uuid>
map experiment start --id <exp-uuid>
map experiment pre-complete --id <exp-uuid> --metadata ./evidence.yaml
map experiment complete --id <exp-uuid> --summary "..." --file ./log.md --metadata ./evidence.yaml
map experiment complete --id <exp-uuid> --summary "..." --log-file-path map/experiments/<slug>/log.md  # 文件引用模式
# --file/--log-file-path 均省略时，可从非交互式 stdin 读取 log
generate-log | map experiment complete --id <exp-uuid> --summary "..." --metadata ./evidence.yaml
map experiment logs --id <exp-uuid>
map experiment log --id <exp-uuid> --summary "..." --file ./log.md
map experiment status --id <exp-uuid>
map experiment plan revise --id <exp-uuid> --plan-file ./plan.md
map experiment accept-result --id <exp-uuid> --summary "..." --file ./review.md   # reviewer 执行
map experiment reject-result --id <exp-uuid> --summary "..." --file ./review.md   # reviewer 执行
```

`topic comment`、`experiment comment`、`experiment log` 和
`experiment complete` 支持隐式 stdin：没有显式正文参数且 stdin 为管道时，CLI
自动读取文本；显式 `--body` / `--file`（实验日志另含 `--log-file-path`）优先。
交互式终端不会阻塞等待 stdin；空输入或超过 1,048,576 个字符时退出并提示改用 `--file`。

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

行动项负责人在完成工作后，应在来源话题写发言、开关联实验；轻量执行项（`action-items.yaml`）用 `map topic action-item complete --evidence ...` / `cancel --reason ...` 清零，close 门禁校验无 open 才放行（CLI 无单独 resolve 命令）。

@ 未匹配时评论仍会发布，响应含 `unresolved_mentions`，并发 `mention.unresolved` 通知给作者。该机制保留于实验评论域（comment 走 API）；FS 话题发言（纯本地写）中的 `@` 仅是视觉提示，不产生 mention 待办。

## 用户反馈（MAP 工具 bug / 改进建议）

MAP 工具本身的反馈走 GitHub issue（旧平台 inbox 已于 v0.15 M62 退役，`feedback submit/list/get/update` 均 exit 2）：

```bash
map feedback --type bug --title "..." --body "复现步骤..."   # 生成预填 issue 链接
map feedback --type idea --title "..."                       # 改进建议
map feedback --open                                          # 直接拉起浏览器
```

离线命令（不连 API），自动附环境信息（map 版本 / Python / OS / api_url）；私有 fork 用 `--repo owner/name` 覆盖。Agent 拿到 URL 后转交人类提交。项目内部 dogfood 反馈仍走 MAP 话题（由 host 开）。
