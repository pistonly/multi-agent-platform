# MAP Runtime Waker

`map-runtime-waker` is the low-token Agent Runtime integration path. It differs
from the older bridge runners: it does not build full task prompts, does not ask
the model to return JSON for the bridge to write, and does not duplicate host /
participant / reviewer business logic.

The waker only:

- polls `map --persona <name> todos`
- derives small wake events
- de-duplicates events in `.map/runtime-waker-state.json`
- resumes the persona's runtime session when a new event exists
- sends a short prompt containing the event id and CLI rules

The resumed runtime agent then uses project skills and `map --persona <name>`
CLI commands to inspect current MAP state and perform the work.

Supported runtime backends:

- `claude`: `PersonaAgentClient` — in-process `ClaudeSDKClient` with `resume=<session_id>`
- `codex`: `openai_codex.Codex.thread_start/thread_resume`

## Usage

```bash
./scripts/start-runtime-waker.sh --persona host
MAP_RUNTIME_PERSONA=reviewer ./scripts/start-runtime-waker.sh
MAP_RUNTIME_BACKEND=codex ./scripts/start-runtime-waker.sh --persona host
./scripts/start-runtime-waker.sh --once --dry-run
```

Useful environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAP_RUNTIME_PERSONA` | `host` | Persona to wake |
| `MAP_RUNTIME_INTERVAL` | `30` | Polling interval |
| `MAP_RUNTIME_STATE_FILE` | `.map/runtime-waker-state.json` | Runtime session + event state |
| `MAP_RUNTIME_HOME` | backend-specific | Runtime home passed to Claude or Codex |
| `MAP_RUNTIME_BACKEND` | `claude` | Runtime backend: `claude` or `codex` |
| `MAP_RUNTIME_MAX_WAKES_PER_CYCLE` | `3` | Hard cap per cycle |
| `MAP_RUNTIME_COOLDOWN_SECONDS` | `300` | Retry cooldown for failed events |
| `MAP_RUNTIME_FORCE` | `0` | Re-wake already seen events |
| `MAP_RUNTIME_MODEL` | unset | Optional runtime model override |
| `MAP_RUNTIME_CODEX_BIN` | unset | Optional Codex binary path for `codex` backend |

When `MAP_RUNTIME_HOME` is not set, the start script uses
`.map/claude-runtime-home` for Claude and `.map/codex-runtime-home` for Codex.

The Codex backend requires the Python Codex SDK package:

```bash
pip install --pre openai-codex
```

The official SDK package is `openai-codex` and is imported as `openai_codex`.
The waker passes the `map-runtime-waker` skill explicitly as a Codex
`SkillInput`, then resumes the saved Codex thread id with
`Codex.thread_resume(...)` on later wake events. The Codex CLI exposes the same
resumable idea through `codex exec resume <SESSION_ID> <PROMPT>`, but this
implementation uses the Python SDK so the backend can manage thread ids and
inputs directly.

## Event Model

The state file stores one resumed session id per persona:

```json
{
  "schema_version": 1,
  "personas": {
    "host": {
      "runtime_session_id": "...",
      "events": {
        "host:pending_topic_reply:<topic_id>:<comment_id>": {
          "status": "woken",
          "last_attempt_at": "..."
        }
      }
    }
  }
}
```

This state is local runtime data and is ignored by Git.

## Session wake logs

Each Claude SDK `session_id` gets an append-only JSONL file under
`.map/runtime-waker-sessions/<session_id>.jsonl`. Every `PersonaAgentClient`
wake records:

- full user `prompt`
- first 200 characters of assistant text (`response_preview`)
- `status`, `persona`, `integration`, timestamp

Disable with `MAP_SESSION_WAKE_LOG=0`. Override directory with
`MAP_SESSION_WAKE_LOG_DIR`.

## Boundary

Keep `map-runtime-waker` as a thin runtime wake-up layer. If persona behavior
changes, update `.cursor/skills/` and let the resumed agent use those skills.
Do not move topic hosting, experiment review, or execution policy back into the
waker.
