# MAP Agent Runtime (unified worktree)

This branch combines:

- **Event-driven wake layer** from `runtime-waker` (`cli/runtime_waker.py`)
- **Long-lived Claude SDK client** from `main` (`cli/agent_client.py` / `PersonaAgentClient`)

## Architecture

```text
map-runtime-waker
  poll todos → discover_wake_events → dedupe → short event prompt
       └── PersonaAgentWakeBackend (process lifetime)
              └── PersonaAgentClient (ClaudeSDKClient + resume)
```

Claude backend holds one in-process `ClaudeSDKClient` per waker process. Session id
is stored as `claude_session_id` / `runtime_session_id` in
`.map/runtime-waker-state.json`.

## Quick start

```bash
cd .claude/worktrees/agent-runtime
./scripts/start-runtime-waker.sh --persona host --once --dry-run
```

See [docs/MAP-RUNTIME-WAKER.md](docs/MAP-RUNTIME-WAKER.md).

## Status

- Target integration path for MAP × Agent Runtime
- Legacy bridge workers remain for compatibility; prefer `map-runtime-waker` here
