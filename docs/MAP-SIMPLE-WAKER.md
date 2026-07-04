# MAP Simple Waker

`map-simple-waker` is a thin alternative to `map-runtime-waker`. It polls
`GET /agents/me/topic-progress`, `GET /agents/me/todos`, and unread notifications
on a fixed cadence and sends one unified remind prompt to a long-lived Agent
Runtime session when work exists.

## Design

| | `runtime-waker` | `simple-waker` |
| --- | --- | --- |
| Trigger | SSE + per-item fingerprints | Poll topic-progress / todos / notifications |
| Prompt | Per-item wake hint + kind routing | Single remind with **topic new-comment excerpts** |
| Session | Context reset per MAP object | One session per persona |
| Agent rule | One item per wake | Batch until `topic progress` + `todos` clear or blocked |
| State file | `.map/runtime-waker-state-*.json` | `.map/simple-waker-state-*.json` |

The waker does **not** write MAP, run experiments, or make business decisions.
The resumed agent reads Skills and uses `map --persona <name>` CLI.

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

Agents mirror this with `map topic progress`. **`map todos`** exposes the same
obligation kinds in named buckets (`pending_topic_replies`, `pending_round_acks`,
`mentions`); simple-waker polls both endpoints.

**Remind buckets:** `my_open_topics` is passive inventory; simple-waker does
**not** remind on it alone. Topic participation is driven by **topic-progress**
(and obligation rows in `todos`).

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
./scripts/start-simple-waker.sh --once --dry-run
```

Environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
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
2. Run `map --persona <name> persona whoami`, **`map --persona <name> topic progress`**, and `map --persona <name> todos`
3. Participate in open topics (host: reply threads + advance rounds; participant: comment + ack)
4. Handle **all** current pending todos (may batch related work)
5. Finish when `topic progress` and `map todos` are empty or every item has a documented blocker
6. Never skip work based on session memory

## When to use which waker

- **simple-waker** (default via `./scripts/start-all-wakers.sh`): local dogfood, topic-progress driven participation, agent-driven batching
- **runtime-waker** (legacy): `MAP_USE_LEGACY_WAKER=1 ./scripts/start-all-wakers.sh` or `./scripts/start-all-wakers-legacy.sh` — SSE latency, per-item `inbound_event` audit, action_item escalation hooks

Both can coexist during migration; use separate state files per persona.
