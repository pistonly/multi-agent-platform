# MAP Agent Runtime (unified worktree)

This branch combines:

- **Default wake layer**: **simple-waker** (`cli/simple_waker.py`) — poll topic-progress + todos + wakeable notifications → unified remind
- **Legacy wake layer**: **runtime-waker** (`cli/runtime_waker.py`) — SSE + per-item fingerprints + `inbound_event` audit (`MAP_USE_LEGACY_WAKER=1`)
- **Pluggable runtime backends** (legacy path): Claude (`PersonaAgentClient`), Codex, or Cursor SDK

## Architecture

```text
simple-waker (default)
  poll topic-progress + todos + notifications → unified remind → PersonaAgentClient session

map-runtime-waker (legacy)
  poll todos → discover_wake_events → dedupe → short event prompt
       ├── PersonaAgentWakeBackend (claude) — one ClaudeSDKClient per process
       ├── CodexSdkWakeBackend — thread_start / thread_resume per wake
       └── CursorSdkWakeBackend — Agent.create / Agent.resume per wake
```

Session id is stored as `claude_session_id` / `runtime_session_id` in
`.map/simple-waker-state-*.json` (default) or `.map/runtime-waker-state-*.json` (legacy).

## Quick start

```bash
# Default: simple-waker
./scripts/start-all-wakers.sh
./scripts/start-simple-waker.sh --persona host --once --dry-run

# Legacy: runtime-waker with Cursor SDK backend
MAP_USE_LEGACY_WAKER=1 MAP_RUNTIME_BACKEND=cursor ./scripts/start-runtime-waker-claude.sh --persona host --once --dry-run
```

See [docs/MAP-SIMPLE-WAKER.md](docs/MAP-SIMPLE-WAKER.md) (default) and
[docs/MAP-RUNTIME-WAKER.md](docs/MAP-RUNTIME-WAKER.md) (legacy) for credentials,
models, and backend-specific setup.

## Status

- Target integration path for MAP × Agent Runtime: **simple-waker** default
- Legacy bridge workers and **runtime-waker** remain for SSE audit / per-item wake debugging
