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
[map-project-collab Skill (§ Waker 模式)](../.agent/skills/map-project-collab/SKILL.md).

## Usage

```bash
./scripts/start-simple-waker.sh --persona host
MAP_SIMPLE_PERSONA=reviewer ./scripts/start-simple-waker.sh
./scripts/start-all-simple-wakers.sh
./scripts/start-all-simple-wakers.sh --drain-topics
./scripts/start-simple-waker.sh --once --dry-run

# Cursor SDK local runtime (requires `pip install -e '.[cursor-runtime]'`)
MAP_SIMPLE_RUNTIME=cursor ./scripts/start-simple-waker.sh --persona host --once --dry-run
MAP_SIMPLE_RUNTIME=cursor ./scripts/start-all-simple-wakers.sh
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
| `MAP_SIMPLE_RUNTIME` | `claude` | Agent runtime: `claude` or `cursor` (`--runtime`) |
| `MAP_SIMPLE_RUNTIME_HOME` | `.map/claude-runtime-home` | Claude runtime HOME (ignored for `--runtime cursor`) |
| `MAP_SIMPLE_MODEL` | unset | Optional model override |

Logs: `.map/simple-waker-logs/` when using `start-all-simple-wakers.sh`.

Session transcripts: `.map/runtime-waker-sessions/` for both Claude and Cursor backends.

### Agent runtime backends

simple-waker reminds a long-lived Agent Runtime. The poll/remind loop is runtime-agnostic; only the backend that receives the prompt changes.

| `--runtime` / `MAP_SIMPLE_RUNTIME` | Backend | Session id in state | Skills |
| --- | --- | --- | --- |
| `claude` (default) | `PersonaAgentWakeBackend` → `claude-agent-sdk` | `claude_session_id` / `runtime_session_id` | Copied into `MAP_SIMPLE_RUNTIME_HOME/.claude/skills` |
| `cursor` | `CursorSdkWakeBackend` → `cursor-sdk` local agent | `cursor_agent_id` / `runtime_session_id` | Project `.cursor/skills` via `setting_sources=["project"]` |

Cursor **must** use the local runtime (this checkout + `.map/` tokens). Switching runtime on an existing state file starts a fresh session.

Install the optional extra before `--runtime cursor`:

```bash
pip install -e ".[cursor-runtime]"
```

### Claude SDK credentials for resumed agents (`.map/.claude-env`)

Wake/invoke only produces the remind prompt; session runner clones the actual agent via
`PersonaAgentClient`, which needs Claude Agent SDK connection info (base URL, token,
model) in the subprocess environment. The project file **`.map/.claude-env`** uses
literal `export VAR=...` lines (the whole `.map/` dir is gitignored — do not commit):

```bash
# .map/.claude-env — LLM keys are authoritative from this file (see below)
export ANTHROPIC_BASE_URL=http://llm-gateway.example:8001
export ANTHROPIC_AUTH_TOKEN=empty
export ANTHROPIC_MODEL=claude-sonnet-4-6
export MAP_RUNTIME_EFFORT=medium
```

Resolved keys: credentials `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` /
`ANTHROPIC_BASE_URL`, models `ANTHROPIC_MODEL` / `CLAUDE_MODEL` plus
`ANTHROPIC_DEFAULT_*` / `ANTHROPIC_SMALL_FAST_MODEL`.

All Claude entry points now use the same selection: explicit `host invoke --env-file`
or `runtime check --env-file` > `MAP_CLAUDE_ENV_FILE` > project `.map/.claude-env`.
An explicit missing/unreadable file fails rather than silently switching accounts.
No sibling repository is searched automatically. The chosen file is authoritative
for credentials, endpoint and model aliases: absent LLM keys are suppressed in the
SDK child's environment, so inherited shell values cannot select another account.
An explicit `--model` still overrides the configured model. With no selected file,
legacy process-env > shell-rc fallback remains available.

Effort is resolved separately: `host invoke --effort` > process `MAP_RUNTIME_EFFORT`
> process `CLAUDE_CODE_EFFORT_LEVEL` > file `MAP_RUNTIME_EFFORT` > file
`CLAUDE_CODE_EFFORT_LEVEL` > `medium`. The value reaches both the SDK CLI option
and child environment; unsupported gateway values are reported, not silently retried
with another model. For a shared file across projects, set `MAP_CLAUDE_ENV_FILE` to
its path rather than copying credentials or writing a custom launcher.

`map runtime check --persona participant [--env-file <path>]` reports the selected
file, model, effort, credential **key names**, SDK availability and repair hints.
It does not expose token/endpoint values, connect to the gateway, create a session or
modify runtime state. `configured` proves local configuration presence only.
Isolated persona homes do not automatically share the shell user's Claude login.

`host invoke` returns nonzero for `error`, `no_response` and `timeout`, in both text
and JSON modes (including global `--json`). JSON includes the runtime error details;
`--follow` still writes progress to stderr. A successful invocation is not task or
review acceptance: check the actual artifacts and MAP state.
This is Claude SDK credentials — distinct from the MAP platform API token
(`~/.map/config.yaml`).

### Cursor SDK credentials for resumed agents (`.map/.cursor-env`)

`--runtime cursor` uses the Cursor Python SDK (`cursor-sdk`) against the local
working tree. Credentials live in **`.map/.cursor-env`** (gitignore, do not
commit):

```bash
# .map/.cursor-env — Cursor keys are authoritative from this file
export CURSOR_API_KEY=cursor_...
export CURSOR_MODEL=composer-2.5
```

When the file exists, simple-waker treats it as authoritative for
`CURSOR_API_KEY` / `CURSOR_MODEL` (`cli.cursor_wake_backend.apply_project_cursor_env`),
including unsetting keys the file does not define. Missing file falls back to
the process environment (`CURSOR_API_KEY` is also accepted by the SDK itself).
Default model is `composer-2.5`. Mint keys at
[Cursor Dashboard → Integrations](https://cursor.com/dashboard/integrations).

The Cursor backend launches `AsyncClient.launch_bridge` and keeps one local
agent per persona (`Agent.create` / `Agent.resume`). It does **not** isolate
`HOME` or copy skills into `.claude/skills`; project Skills load from
`.cursor/skills` via `setting_sources=["project"]`. `host invoke` / `map runtime chat`
still use the Claude client in this change.

## Runtime Skill 源与契约版本（v3 → v4）

Skill 真身在 **`.agent/skills/`**（中立目录）。`.cursor/skills`、`.claude/skills`、
`.codex/skills` 三者都是指向 `../.agent/skills/` 的符号链接，只为让各 runtime 的
自动发现与历史文档链接继续可用——**不要**单独同步或改写这三个链接。

`cli/simple_waker.py` 用两组常量描述「可能影响 Agent 行为的运行时事实」：

| 常量 | 作用 |
|------|------|
| `RUNTIME_CONTRACT_FILES` | 参与哈希的清单，v4 起为 `.agent/skills/<skill>/SKILL.md` 五条（map-project-collab / topic-host / topic-participant / experiment-host / experiment-reviewer） |
| `RUNTIME_CONTRACT_VERSION` | 版本盐值，随清单语义一起递增；当前 `simple-waker-runtime-contract-v4` |

两者共同产出 `runtime_contract_hash`，写进 persona state 供对比（见
`cli/simple_waker.py:runtime_contract_hash`）。

### 契约版本变更需重启 waker

哈希在 `SimpleWaker.__init__` 计算**一次**并缓存在实例上，运行期不重算。因此：

- 升级后仍在跑的 waker 会继续持有 **v3 时期的旧哈希**，而漂移检测的源已变成
  `.agent/skills/**`。此时它可能把新路径判成 drift 并按旧内容静默回写，或反过来
  对本应生效的清单变更视而不见。
- 正确姿势：**改完契约（清单或版本号）→ 重启 waker**
  （`scripts/start-simple-waker.sh --persona <name>`，或 `scripts/start-all-simple-wakers.sh`），
  让冷启动的 `_startup_sync_with_audit` 以新清单重新镜像一次。
- 审计留痕：启动日志的 `startup_sync` 事件含 `skills_count` / `synced_skills` /
  `skipped_reason`，重启后应看到 `skills_count=6`。

镜像语义未因迁目录而改变：`sync_runtime_skills` 仍是「全量镜像
（rmtree + copytree + 孤儿清理）」，源只读、目标只在 `<runtime_home>/.claude/skills`
之下，项目里的三个符号链接不参与 rmtree/copytree，不会被删成普通目录或断链。

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
- simple-waker 每个 cycle 的 work 轮询带 `--client waker` 特征标记（T24 起默认走
  in-process `cli/map_sdk_client.py`，回退子进程 `cli/map_command_client.py`），
  两条路径均透传 server；人工 `map work` 不带此参数。
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
- `MAP_USE_LEGACY_WAKER=1` 不再生效（旧编排脚本已删除，统一 `start-all-simple-wakers.sh`）
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
