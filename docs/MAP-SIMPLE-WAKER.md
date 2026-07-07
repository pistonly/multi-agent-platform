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
[map-runtime-waker Skill](../.cursor/skills/map-runtime-waker/SKILL.md).

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
close topics. In this mode, `topic dismiss` only hides a todo and does **not**
count as progress because the topic remains open. Timeout defaults to 7200 seconds and can be changed with
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

## Agent rules

On remind, the agent should:

1. Read `map-runtime-waker` → `map-project-collab` → persona Skill
2. Run `map --persona <name> persona whoami`, **`map --persona <name> work`**（或 `topic progress` + `todos`）
3. Participate in open topics (host: reply threads + advance rounds; participant: comment + ack)
4. Handle **all** current pending todos (may batch related work)
5. Finish when `map work`（或 topic progress + todos）is empty or every item has a documented blocker
6. Never skip work based on session memory

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
