# MAP Simple Waker

`map-simple-waker` is the default MAP waker. It polls
`GET /agents/me/work` (unified snapshot: whoami + topic-progress + todos +
wakeable notifications) on a fixed cadence and sends one unified remind prompt
to a long-lived Agent Runtime session when work exists. After each successful
remind it writes an aggregated `inbound_event` audit row and advances the
`action_item` escalation timeline (WAKE → `mark-wake-sent`, STALE → `mark-stale`).

## Design

| Property | Value |
| --- | --- |
| Trigger | Poll `GET /agents/me/work` (topic-progress + todos + wakeable notifications) |
| Prompt | Single remind with **work_items kinds + unread excerpts** |
| Session | One session per persona |
| Agent rule | Batch until `map work`（或 topic progress + todos）clear or blocked |
| State file | `.map/simple-waker-state-*.json` |
| Audit | Aggregated `inbound_event` per remind (fingerprint=`simple-remind:{persona}:{ts}`) |
| action_item escalation | Scans `todos.action_items` before remind: WAKE → `action mark-wake-sent`, STALE → `action mark-stale`, SKIP → skip |
| experiment lock alert | Calls `map experiment lock scan-stalled` before polling work; stalled running locks materialize as wakeable notifications for holder/host and digest notifications for other project members |

The waker does **not** write MAP, run experiments, or make business decisions.
The resumed agent reads Skills and uses `map --persona <name>` CLI.

### Experiment lock no-progress alerts

`simple-waker` asks the platform to scan stalled running experiment locks before
each `map work` poll. The platform owns the threshold and recipient policy:

- holder/host receives `experiment.lock.no_progress` as `wakeable`, so
  `map work --notification-category wakeable` can remind the responsible runtime.
- participant/reviewer/project members receive the same event as `digest`, so
  they can inspect it manually without being woken as an obligation.
- released locks, experiments no longer in `running`, or locks with execution
  logs after `lock_acquired_at` do not emit no-progress notifications.

### Topic progress (platform)

`GET /agents/me/topic-progress` is a **projection** of per-agent **topic work items**
(`topic_work_items_for_agent` in the API). Each open topic may include:

| Field | Meaning |
| --- | --- |
| `work_items[]` | Obligation + contextual items (`pending_topic_reply`, `round_ack`, `mention`, `unread_change`) |
| `new_comments[]` | Legacy projection of `unread_change` items (prefer `work_items` for new consumers) |

**Inclusion rules (summary):**

- Host/creator: obligation items always surface; dismissed topics hidden until new activity.
- Participant/reviewer: topics with obligation items, or prior participation, or @mention.
- Reviewer cold-start: contextual-only open topics are omitted.

Agents mirror this with `map work` or `map topic progress`. **`map todos`** exposes the same
obligation kinds in named buckets (`pending_topic_replies`, `pending_round_acks`,
`mentions`) plus periodic host follow-up (`stale_open_topics`, host-owned open topics
with no activity for 30 minutes); simple-waker polls the unified **`/agents/me/work`**
endpoint.

**Remind buckets:** `my_open_topics` is passive inventory; simple-waker does
**not** remind on it alone. Periodic host review uses `stale_open_topics` instead.
Topic participation is driven by **topic work items** (topic-progress / `map work`,
and obligation rows in `todos`).

### Reviewer scheduling (P4)

When a reviewer has `pending_review` or `pending_result_review`, simple-waker
**does not remind** on topic **contextual** items (`unread_change`). Obligation
items (e.g. `@mention`, `round_ack`) still remind. See
[map-project-collab Skill (§ Waker 模式)](../.cursor/skills/map-project-collab/SKILL.md).

## Usage

```bash
./scripts/start-simple-waker.sh --persona host
MAP_SIMPLE_PERSONA=reviewer ./scripts/start-simple-waker.sh
./scripts/start-all-simple-wakers.sh
./scripts/start-all-wakers.sh --drain-topics
./scripts/start-simple-waker.sh --once --dry-run
```

`--drain-topics` starts host/participant/reviewer wakers and monitors
`map topic list --status open` until the open-topic count reaches zero, then
stops the wakers. It does not embed topic-hosting policy: the resumed agents
still read Skills and use `map` CLI to comment, advance rounds, resolve, or
close topics. In this mode, the host should actively drive open topics toward
discussion, clarification, decisions, action items, experiment boundaries, or a
well-grounded close reason. Timeout defaults to 7200 seconds and can be changed with
`MAP_WAKER_DRAIN_TIMEOUT_SECONDS`; check cadence defaults to 60 seconds and can
be changed with `MAP_WAKER_DRAIN_CHECK_INTERVAL_SECONDS`.

Environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAP_WAKER_DRAIN_TIMEOUT_SECONDS` | `7200` | Max runtime for `--drain-topics` before exit 124 |
| `MAP_WAKER_DRAIN_CHECK_INTERVAL_SECONDS` | `60` | Open-topic polling cadence in `--drain-topics` |
| `MAP_SIMPLE_PERSONA` | `host` | Persona to wake |
| `MAP_SIMPLE_ACTIVE_INTERVAL` | `30` | Poll/remind cadence while work exists |
| `MAP_SIMPLE_IDLE_INTERVAL` | `300` | Poll cadence when idle |
| `MAP_SIMPLE_MIN_REMIND_SECONDS` | `30` | Minimum gap between remind prompts |
| `MAP_SIMPLE_STATE_FILE` | `.map/simple-waker-state.json` | Session + remind timestamps |
| `MAP_SIMPLE_RUNTIME_HOME` | `.map/claude-runtime-home` | Claude runtime HOME |
| `MAP_SIMPLE_MODEL` | unset | Optional model override |

Logs: `.map/simple-waker-logs/` when using `start-all-simple-wakers.sh`.

Session transcripts: same `.map/runtime-waker-sessions/` path as the Claude
backend (`PersonaAgentClient`).

### Claude SDK credentials for resumed agents (`.map/.claude-env`)

Wake/invoke only produces the remind prompt; session runner clones the actual agent via
`PersonaAgentClient`, which needs Claude Agent SDK connection info (base URL, token,
model) in the subprocess environment. When not set on the current process env, they
fall back to the `export VAR=...` lines in **`.map/.claude-env`** (the whole
`.map/` dir is gitignored — do not commit):

```bash
# .map/.claude-env — used only when process env vars are unset
export ANTHROPIC_BASE_URL=http://192.168.20.32:8001
export ANTHROPIC_AUTH_TOKEN=empty
export ANTHROPIC_MODEL=claude-sonnet-4-6
```

Resolved keys: credentials `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` /
`ANTHROPIC_BASE_URL`, model `ANTHROPIC_MODEL` / `CLAUDE_MODEL`. Resolution
order: **process env > `.map/.claude-env` > `~/.bashrc` and other shell rc**.
This is Claude SDK credentials — distinct from the MAP platform API token
(`~/.map/config.yaml`).

## Agent rules

On remind, the agent should:

1. Read `map-project-collab` (§ Waker 模式) → persona Skill
2. Run `map --persona <name> persona whoami`, **`map --persona <name> work`**（或 `topic progress` + `todos`）
3. Participate in open topics (host: reply threads + advance rounds; participant: comment + ack)
4. Handle **all** current pending todos (may batch related work)
5. Finish when `map work`（或 topic progress + todos）is empty or every item has a documented blocker
6. Never skip work based on session memory

## Waker 心跳可见性与降级路径（waker-heartbeat-visibility 实验）

simple-waker 是 FS 话题轮次推进后的主唤醒链路，但 waker 状态只存在本地文件里——
平台侧看不到 waker 是否存活，停机时 advance-round 照常生成通知却无人消费，所有等表态
话题静默挂起且无告警。本方案在平台侧做心跳可见性 + 告警。

### 心跳记录点与判定（服务器侧）

- `agents` 表新增双时间戳：`last_api_seen_at`（任意 `GET /agents/me/work` 刷新）
  与 `last_waker_poll_at`（仅带 `--client waker` 特征标记的轮询刷新）。
- simple-waker 每个 cycle 的 `map work` 子进程带 `--client waker`（
  `cli/map_command_client.py`），CLI 透传 server；人工 `map work` 不带此参数。
- stale 判定只看 `last_waker_poll_at`：降级场景（invoke 补位期间被唤醒 agent
  频繁手动跑 `map work`）只刷新 `last_api_seen_at`，**不污染** waker 存活判定。
- stale =「曾有心跳（`last_waker_poll_at` 非 null）AND 距今超过阈值
  `MAP_WAKER_STALE_THRESHOLD_MINUTES`（默认 15min）」。null（从未心跳）→
  `never`，不 WARN——「只挂一个 waker」的部署形态下其余 persona 不永久告警。
- `GET /status` 的 `waker_heartbeats[]` 返回 per-agent 行（agent_id /
  agent_name / persona / last_waker_poll_at / stale，服务器算好的字段）；`map work`
  顶部渲染全部 persona waker 状态，**CLI 只渲染不复制判定逻辑**（stale 输出
  `[WARN] waker heartbeat stale for <agent>(<persona>)`，渲染到 stderr，
  stdout 保持纯净 YAML）。

### 降级路径：waker 不可用 → host invoke 编排

当 `[WARN] waker heartbeat stale` 出现，说明对应 persona 的 waker 已停机/心跳停止，
话题推进的自动唤醒链路失效。此时降级到 **host invoke 手动编排**：

1. 停止已失效的 waker（`map server stop`，避免它恢复后重复布唤醒）。
2. 用 host persona 对受影响话题手动 `map topic advance-round / --ready` 推进轮次，
   并用 `map --persona <p> work|todos` 检查各 persona 待办，逐项处理
   （等同 waker 被唤醒后 Agent 的行为）。
3. 修好 waker 后重启（`map server start` 或 `scripts/start-simple-waker.sh`），
   它下一次带 `--client waker` 的轮询会刷新 `last_waker_poll_at`，告警自动消失。

### 层次：waker-stale WARN 是 stale_open_topics 检测的前置信任条件

- `stale_open_topics`（话题停滞检测，`MAP_STALE_OPEN_TOPIC_THRESHOLD_MINUTES`）
  判定「话题很久没进展」；waker-stale WARN 判定「waker 很久没心跳」。
- 两者互补：话题停滞可能因为 waker 挂（无人消费通知），也可能因为真无人表态。
  waker-stale WARN 提供**前置信任**——先确认唤醒链路健康，再谈话题是否真停滞。
  waker 停机期间的 stale_open_topics 会是**假象**（没人被唤醒去推进），此时
  waker-stale WARN 恰好帮人区分「waker 挂了」与「话题真卡住」。

### 边界：只覆盖「waker 挂、server 活」

- 本方案只兜 **waker 挂、server 活** 这半边故障：心跳存在 server 的
  `agents` 表（时间戳持久化，server 重启不丢，A4 冷启动无误报窗口）。
- **server 挂** 是显性故障（API/Web 直接不可用，报错明显），无需心跳兜底——
  平台都没了还谈 waker 心跳无意义。
- v1 不覆盖：waker 上报自己的轮询周期/双档 interval（server 无从得知，固定
  15min 阈值 ≈3× 默认 idle 周期即可）；「从未配置 waker」与「配置了但从未成功
  轮询」在 v1 都归为 `never`，v2 可演进区分。

## Legacy runtime-waker (deprecated)

`cli/runtime_waker.py` 保留作为 re-export 兼容层，原 SSE + 逐项 fingerprint +
`inbound_event` 审计的启动路径已退役：

- `scripts/start-runtime-waker-claude.sh`、`scripts/start-all-wakers-legacy.sh` 已删除
- `MAP_USE_LEGACY_WAKER=1` 不再生效（`start-all-wakers.sh` 现直接调用 simple-waker）
- `docs/MAP-RUNTIME-WAKER.md` 已删除
- 原 `ActionItemWakeDecision` / `should_wake_action_item` /
  `scan_pending_action_items` 已迁至 `cli/action_item_escalation.py`
- 原 `PersonaAgentWakeBackend` / `sync_runtime_skills` / `TODO_WAKE_BUCKETS` /
  `TODO_BUCKET_UI_LABELS` 已迁至 `cli/wake_backend.py`
- 既有 `from cli.runtime_waker import ...` 仍可用（re-export），新代码请直接
  导入新模块

## Deprecated 脚本 stub 与第二阶段删除纪律（实验 124e9a00）

`start-*-bridge*.sh` 四个 deprecated 启动脚本已 stub 化（exit 1 + 指引
simple-waker 替代），`scripts/check-deprecated.sh` 在 CI 执行两条防回潮规则
（stub 内容校验 + 声明处登记校验），登记单一真相为
`docs/LEGACY-ENTRY-MATRIX.md`。

第二阶段物理删除纪律：删除 deprecated 脚本前须走**显式 diff 评审**——
host 之外至少一人（participant/reviewer 或用户）看过删除清单并表态；
观察期内若发现隐藏引用（cron/systemd/习惯路径）先登记再裁决，不盲删。
