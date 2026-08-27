# MAP Agent Runtime

Default wake layer: **simple-waker** (`cli/simple_waker.py`) — poll
topic-progress + todos + wakeable notifications → unified remind → Agent Runtime.

```text
simple-waker
  poll map work → unified remind
       ├── --runtime claude (default)
       │     PersonaAgentWakeBackend — one ClaudeSDKClient per process
       └── --runtime cursor
             CursorSdkWakeBackend — AsyncClient.launch_bridge + local Agent.create / Agent.resume
```

Session ids are stored per persona in `.map/simple-waker-state-*.json`:

- Claude: `claude_session_id` / `runtime_session_id`
- Cursor: `cursor_agent_id` / `runtime_session_id`
- Both write `runtime_backend` so switching runtimes starts a fresh session

Legacy `runtime-waker` / host-bridge paths are retired. Do not use
`MAP_USE_LEGACY_WAKER` or `scripts/cursor-*-runner.py` (old JSON bridge
contract) for new work.

## Quick start

```bash
# Default: Claude Agent SDK
./scripts/start-all-simple-wakers.sh
./scripts/start-simple-waker.sh --persona host --once --dry-run

# Cursor SDK local runtime
pip install -e ".[cursor-runtime]"
MAP_SIMPLE_RUNTIME=cursor ./scripts/start-simple-waker.sh --persona host --once --dry-run
```

Credentials: `.map/.claude-env` (Claude) or `.map/.cursor-env` (Cursor). See
[docs/MAP-SIMPLE-WAKER.md](docs/MAP-SIMPLE-WAKER.md).

## Status

- Target integration path for MAP × Agent Runtime: **simple-waker**
- Cursor support is waker-only (`--runtime cursor`); `host invoke` and
  `map runtime chat` still use Claude
