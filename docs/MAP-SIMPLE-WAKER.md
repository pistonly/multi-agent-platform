# MAP Simple Waker

`map-simple-waker` is a thin alternative to `map-runtime-waker`. It polls
`GET /agents/me/todos` plus unread notifications on a fixed cadence and sends
one unified remind prompt to a long-lived Agent Runtime session when work
exists.

## Design

| | `runtime-waker` | `simple-waker` |
| --- | --- | --- |
| Trigger | SSE + per-item fingerprints | Poll todos / notifications |
| Prompt | Per-item wake hint + kind routing | Single "check MAP and act" remind |
| Session | Context reset per MAP object | One session per persona |
| Agent rule | One item per wake | Batch until `todos` clear or blocked |
| State file | `.map/runtime-waker-state-*.json` | `.map/simple-waker-state-*.json` |

The waker does **not** write MAP, run experiments, or make business decisions.
The resumed agent reads Skills and uses `map --persona <name>` CLI.

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
2. Run `map --persona <name> persona whoami` and `map --persona <name> todos`
3. Handle **all** current pending items (may batch related work)
4. Finish when `map todos` is empty or every item has a documented blocker
5. Never skip work based on session memory

## When to use which waker

- **simple-waker** (default via `./scripts/start-all-wakers.sh`): local dogfood, fewer moving parts, agent-driven batching
- **runtime-waker** (legacy): `MAP_USE_LEGACY_WAKER=1 ./scripts/start-all-wakers.sh` or `./scripts/start-all-wakers-legacy.sh` — SSE latency, per-item `inbound_event` audit, action_item escalation hooks

Both can coexist during migration; use separate state files per persona.
