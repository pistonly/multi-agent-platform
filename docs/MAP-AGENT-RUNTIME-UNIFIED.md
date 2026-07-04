# MAP Agent Runtime (unified worktree)

This branch combines:

- **Event-driven wake layer** from `runtime-waker` (`cli/runtime_waker.py`)
- **Pluggable runtime backends**: Claude (`PersonaAgentClient`), Codex, or Cursor SDK

## Architecture

```text
map-runtime-waker
  poll todos → discover_wake_events → dedupe → short event prompt
       ├── PersonaAgentWakeBackend (claude) — one ClaudeSDKClient per process
       ├── CodexSdkWakeBackend — thread_start / thread_resume per wake
       └── CursorSdkWakeBackend — Agent.create / Agent.resume per wake
```

Session id is stored as `claude_session_id` / `runtime_session_id` in
`.map/runtime-waker-state.json` (Claude session id, Codex thread id, or Cursor
`agent_id`).

## Quick start

```bash
# Default: Claude backend
./scripts/start-runtime-waker.sh --persona host --once --dry-run

# Cursor SDK backend
MAP_RUNTIME_BACKEND=cursor ./scripts/start-runtime-waker.sh --persona host --once --dry-run

# All three personas
MAP_RUNTIME_BACKEND=cursor ./scripts/start-all-wakers.sh
```

See [docs/MAP-RUNTIME-WAKER.md](docs/MAP-RUNTIME-WAKER.md) for credentials,
models, and backend-specific setup.

## Status

- Target integration path for MAP × Agent Runtime
- Legacy bridge workers remain for compatibility; prefer `map-runtime-waker` here
