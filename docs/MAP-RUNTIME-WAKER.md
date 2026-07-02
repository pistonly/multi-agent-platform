# MAP Runtime Waker

`map-runtime-waker` is the low-token Agent Runtime integration path. It differs
from the older bridge runners: it does not build full task prompts, does not ask
the model to return JSON for the bridge to write, and does not duplicate host /
participant / reviewer business logic.

The waker only:

- subscribes to a Server-Sent Events (SSE) long-poll on
  `GET /agents/me/notifications/stream` for real-time wake events
- derives small wake events from SSE frames **and** from the periodic
  `MAP_RUNTIME_INTERVAL` polling fallback (`map --persona <name> todos` +
  unread notifications)
- de-duplicates events in `.map/runtime-waker-state.json`
- resumes the persona's runtime session when a new event exists
- sends a short prompt containing the event id and CLI rules

The SSE subscription lives in the waker process itself — a plain Python
loop independent of the runtime backend. All three backends (`claude`,
`codex`, `cursor`) benefit identically: per-wake-started Cursor / Codex
agents do **not** need to maintain their own SSE connection because the
waker already covers them.

The polling fallback is **last-line defense and is never disabled** —
SSE can drop, the API server can restart, the network can partition. The
fallback ensures the waker still self-heals after any of those.

The resumed runtime agent then uses project skills and `map --persona <name>`
CLI commands to inspect current MAP state and perform the work.

Supported runtime backends:

- `claude`: `PersonaAgentClient` — in-process `ClaudeSDKClient` with `resume=<session_id>`
- `codex`: `openai_codex.Codex.thread_start/thread_resume`
- `cursor`: `cursor_sdk.Agent.create` / `Agent.resume` — local agent per wake, session id is `agent_id` (`agent-...`)

## Usage

```bash
./scripts/start-runtime-waker.sh --persona host
MAP_RUNTIME_PERSONA=reviewer ./scripts/start-runtime-waker.sh
MAP_RUNTIME_BACKEND=codex ./scripts/start-runtime-waker.sh --persona host
MAP_RUNTIME_BACKEND=cursor ./scripts/start-runtime-waker.sh --persona host
MAP_RUNTIME_BACKEND=cursor ./scripts/start-all-wakers.sh
./scripts/start-runtime-waker.sh --once --dry-run
```

Useful environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MAP_RUNTIME_PERSONA` | `host` | Persona to wake |
| `MAP_RUNTIME_INTERVAL` | `600` | Polling fallback interval (seconds). SSE 长连为主路径；此变量仅控制兜底轮询节奏 |
| `MAP_RUNTIME_STATE_FILE` | `.map/runtime-waker-state.json` | Runtime session + event state |
| `MAP_RUNTIME_HOME` | backend-specific | Runtime home passed to Claude or Codex (ignored by `cursor`) |
| `MAP_RUNTIME_BACKEND` | `claude` | Runtime backend: `claude`, `codex`, or `cursor` |
| `MAP_RUNTIME_MAX_WAKES_PER_CYCLE` | `3` | Hard cap per cycle |
| `MAP_RUNTIME_COOLDOWN_SECONDS` | `300` | Retry cooldown for failed events |
| `MAP_RUNTIME_HEARTBEAT_SECONDS` | same as `MAP_RUNTIME_INTERVAL` | Re-wake interval when todos still show pending work after agent finished a wake without advancing MAP state |
| `MAP_RUNTIME_PERSONA_INFLIGHT_SECONDS` | `1800` | After a successful wake, skip ALL of this persona's events for this many seconds so a background session is not preempted by a sibling event waking into a different context (single-flight). `0` disables |
| `MAP_RUNTIME_WOKEN_COOLDOWN_SECONDS` | `1800` | Fallback self-heal TTL when heartbeat is not set (CLI/tests) |
| `MAP_RUNTIME_FORCE` | `0` | Re-wake already seen events |
| `MAP_RUNTIME_MODEL` | unset | Optional runtime model override |
| `MAP_RUNTIME_CODEX_BIN` | unset | Optional Codex binary path for `codex` backend |
| `MAP_RUNTIME_SSE_ENABLED` | `1` | Enable SSE long-poll primary path. `0` falls back to polling-only (escape hatch) |
| `MAP_RUNTIME_SSE_CONNECT_TIMEOUT_SECONDS` | `10` | SSE handshake connect timeout |
| `MAP_RUNTIME_SSE_READ_TIMEOUT_SECONDS` | unset | SSE per-read timeout (defaults to None; rely on server-side keepalive) |
| `MAP_RUNTIME_SSE_BACKOFF_BASE_SECONDS` | `1` | D3 reconnect exponential backoff base (1s, 2s, 4s, …) |
| `MAP_RUNTIME_SSE_BACKOFF_MAX_SECONDS` | `30` | D3 reconnect backoff cap |
| `MAP_RUNTIME_SSE_RECENT_RESUME_WINDOW_SECONDS` | `60` | D4 client-side dedup window — same fingerprint may attempt resume at most once per window |
| `MAP_RUNTIME_SSE_REPLAY_LIMIT` | `200` | D3 reconnect backfill — max unread wakeables re-pulled via `unread_only=true` after reconnect |

When `MAP_RUNTIME_HOME` is not set, the start script uses
`.map/claude-runtime-home` for Claude and `.map/codex-runtime-home` for Codex.
The `cursor` backend does not use `MAP_RUNTIME_HOME`; it runs local agents
against `project_root` via the Cursor SDK bridge.

### Claude backend

Default. Holds one in-process `ClaudeSDKClient` per waker process. Requires
`claude_agent_sdk` and Anthropic credentials (`ANTHROPIC_API_KEY` or
`.map/.claude-env`). When `MAP_RUNTIME_HOME` is set, project skills are synced
into `$MAP_RUNTIME_HOME/.claude/skills/`.

### Codex backend

The Codex backend requires the Python Codex SDK package:

```bash
pip install --pre openai-codex
```

The official SDK package is `openai-codex` and is imported as `openai_codex`.
On each wake the waker injects a **skill chain** as Codex `SkillInput` items
(`map-runtime-waker` dispatcher → `map-project-collab` → persona skill(s)), then
resumes the saved Codex thread id with `Codex.thread_resume(...)` on later wake
events. The Codex CLI exposes the same resumable idea through
`codex exec resume <SESSION_ID> <PROMPT>`, but this implementation uses the
Python SDK so the backend can manage thread ids and inputs directly.

### Cursor backend

The Cursor backend uses the Python Cursor SDK (`cursor-sdk`, imported as
`cursor_sdk`). Each wake creates or resumes a **local** agent; the persisted
session id is `agent.agent_id` (prefix `agent-`), stored in the same state file
fields as other backends (`runtime_session_id` / `claude_session_id`).

```bash
pip install cursor-sdk
export CURSOR_API_KEY="cursor_..."   # or .map/.cursor-env / shell rc export
export CURSOR_MODEL="composer-2.5"   # optional; --model / MAP_RUNTIME_MODEL override
```

On each wake:

1. `Agent.create(...)` when there is no saved session id (or wake context changed)
2. `Agent.resume(agent_id, ...)` when resuming the same MAP object context
3. `agent.send(prompt)` then `run.wait()`

Project skills load through `local.setting_sources=["project"]` (`.cursor/skills/`,
including `map-runtime-waker`). Unlike Codex, the waker does not pass an explicit
`SkillInput`; the agent discovers skills from the repo.

Credentials resolve in order: process environment → `.map/.cursor-env` →
`~/.bashrc` / `~/.profile` / `~/.bash_profile` export lines.

Smoke test (no MAP todos required):

```bash
python3 -m cli.runtime_waker --persona host --backend cursor --once --dry-run
```

## Event Model

Wake events are derived **only** from `GET /agents/me/todos` bucket items plus
unread notifications. Each event's `kind` equals the todos field name (same as
Web UI sections). Fingerprints are stable item ids:
`{persona}:{bucket}:{item_id}`.

Removed: client-side todo cursors, `hosted_topic` comment cursors,
`open_topic_opportunity` gates, and derived experiment/topic fingerprints.

The state file stores the active resumed session per persona and wake context.
When the wake context changes, for example from one topic to another topic or
from one experiment to another experiment, the waker clears the old session id
and starts a fresh runtime session. Events in the same context continue to
resume the existing session.

**Persona single-flight.** The backend resumes one session per persona, and a
context change resets it — so a sibling event selected while a prior wake is
still running would preempt that session (the agent's in-flight edits could be
lost or interleaved). To prevent this, a successful wake stamps
`personas.<persona>.last_woken_at`; until `MAP_RUNTIME_PERSONA_INFLIGHT_SECONDS`
(default `1800`) elapses, every event of that persona is skipped. This
intentionally suppresses `heartbeat` re-wakes during the window, so a
long-running host experiment is not interrupted by a sibling experiment. For
short-task personas (participant/reviewer) that suffer throughput, set a smaller
value. `0` falls back to pure per-event dedup. `force` bypasses the gate.

```json
{
  "schema_version": 1,
  "personas": {
    "host": {
      "runtime_session_id": "...",
      "last_wake_context_key": "topic:<topic_id>",
      "last_wake_object_id": "<topic_id>",
      "events": {
        "host:pending_topic_replies:<comment_id>": {
          "status": "woken",
          "last_attempt_at": "..."
        }
      }
    }
  }
}
```

This state is local runtime data and is ignored by Git.

## SSE long-poll primary path

The waker subscribes to `GET /agents/me/notifications/stream` (Phase 1
endpoint, see `server/api/agents.py`) and treats the SSE stream as the
**primary** event source. `MAP_RUNTIME_INTERVAL` polling is retained as
last-line defense and is never disabled.

### Event routing

Each SSE `notification.created` frame carries `payload_json.kind`. The
waker maps that field to a wake-event kind:

| `payload_json.kind` | wake kind |
| --- | --- |
| `mention` | `pending_mention_reply` |
| `topic.lifecycle` / `topic.comment` / `topic.advance_round` / `topic.resolved` | `topic_lifecycle` |
| `experiment.lifecycle` / `experiment.phase_changed` / `plan.revised` | `experiment_lifecycle` |
| `review.submitted` / `review_item.status_changed` | `pending_review` |
| `comment.created` (on experiment) | `pending_result_review` |

Unknown / unmapped kinds are dropped silently from the SSE stream — the
polling fallback will still surface them. This avoids waking the agent on
a notification that has no corresponding todo bucket.

### D3 — Reconnect compensation

SSE disconnects (network blip, server restart, keepalive timeout) trigger
an exponential backoff before reconnect:

`delay = min(base * 2^attempt, max) + jitter` with default
`base=1s, max=30s`. The attempt counter persists in
`.map/runtime-waker-state-<persona>.json` so a process restart resumes
the backoff where it left off (not a fresh `attempt=0`).

After a successful reconnect the waker pulls **all unread wakeable
notifications** via `notifications_unread` (capped by
`MAP_RUNTIME_SSE_REPLAY_LIMIT`) and dispatches each one as
`event_source="replay"`. This catches events the SSE long-poll missed
during the disconnect window.

Server-side, the `inbound_event.UNIQUE(fingerprint)` table (Phase 1 D6)
is the authoritative cross-process replay gate. Client-side, D4 below
is the cheap in-memory gate that prevents pointless round-trips.

### D4 — Client-side rate limit + replay exemption

The waker keeps a per-fingerprint timestamp of the last resume attempt.
A new event whose fingerprint was seen within
`MAP_RUNTIME_SSE_RECENT_RESUME_WINDOW_SECONDS` (default 60s) is skipped
without calling the server-side `record` endpoint or invoking a backend
resume. This keeps an SSE frame + a polling fallback cycle from
double-resuming the same fingerprint.

The rate limit is bypassed in one case: events arriving via
`event_source="replay"` (D3 backfill). Replay must wake — if the waker
deduplicated reconnect backfill, any event the SSE long-poll missed
during the disconnect would stay missed. The bypass is local to the
client; the server-side `UNIQUE(fingerprint)` gate still rejects
cross-process replays, so the exemption is safe.

### `event_source` taxonomy

Every entry written to `inbound_event.source` (and to the wake session
JSONL) carries exactly one of three values:

- `polling` — sourced from `MAP_RUNTIME_INTERVAL` fallback cycle
- `sse` — sourced from a real-time SSE long-poll frame
- `replay` — sourced from D3 reconnect backfill

Audit three-way joins (Phase 1 A3) work unchanged because the schema is
unchanged — only the value set grew.

### Backend neutrality

The SSE client lives in `cli/runtime_waker.py` and runs as part of the
waker process. It does not depend on Claude / Codex / Cursor SDKs.
Per-wake-started backends (Cursor local agents, Codex CLI invocations)
do **not** maintain their own SSE connection — the waker covers them.
This is why all three backends get identical latency improvements from
Phase 2 without per-backend changes.

## Session wake logs

**Claude backend only:** each Claude SDK `session_id` gets an append-only JSONL
file under `.map/runtime-waker-sessions/`. New sessions use a sortable filename:

`YYYYMMDD-HHMMSS_<persona>_<session_id>.jsonl` (timestamps default to
`Asia/Shanghai`; override with `MAP_LOG_TIMEZONE`). Resume wakes append to the
same file. Legacy plain `<session_id>.jsonl` files are still read if present.

Each `PersonaAgentClient` wake writes a **real-time event stream** to that file
— one JSONL line per event, in order:

`wake` → (`text` | `tool_use` | `tool_result`)* → `result`

so an agent stuck mid-turn (e.g. running a long test) is visible from the
timestamp of the last event. Event lines are summary-level (`tool_use` →
`Bash: pytest tests/ -q`; `tool_result` → `ok: 45 passed`), not full tool I/O
— full content stays in Claude's own session jsonl. The terminal `result` line
keeps the wake-summary fields used by the A3 audit join:

- full user `prompt`
- first 200 characters of assistant text (`response_preview`)
- `status`, `persona`, `integration`, timestamp
- `event_id` / `event_source` / `fingerprint` (A3 join keys)

Disable with `MAP_SESSION_WAKE_LOG=0`. Override directory with
`MAP_SESSION_WAKE_LOG_DIR`. Override log timestamp timezone with
`MAP_LOG_TIMEZONE` (default `Asia/Shanghai`).

Codex and Cursor backends do not write these JSONL files; inspect waker stdout /
`.map/waker-logs/*.log` instead.

## Boundary

Keep `map-runtime-waker` as a thin runtime wake-up layer. If persona behavior
changes, update `.cursor/skills/` and let the resumed agent use those skills.
Do not move topic hosting, experiment review, or execution policy back into the
waker.

## v0.8 保留正则（不在 ROUND_SUMMARY_RE 清理范围）

v0.8 实验 I1 删除 `cli/host_worker_topic.py` 中基于评论正文的 `ROUND_SUMMARY_RE`
fallback，轮次与 Summary 门禁改读 `Topic.discussion_round` / `Topic.round_summary_count`。

以下正则属于**必要校验或业务逻辑**，不在 v0.8 清理范围：

| 正则 / 模式 | 位置 | 用途 |
|-------------|------|------|
| `_EXPORT_RE` | `cli/agent_client.py` | 解析 shell export 行 |
| `_SAFE_SESSION_ID_RE` | `cli/session_wake_log.py` | session id sanitization |
| `PROJECT_KEY_PATTERN` | `sdk/python/map_types/schemas.py` | project_key 校验 |
| `MENTION_PATTERN` | `server/services/mention_service.py` | @ 提及解析 |

## 生产 systemd 部署

在 Linux 生产机上可用 systemd 常驻三 persona waker：

```bash
# 预览动作（不写 unit、不调用 systemctl）
bash scripts/systemd/map-wakers.service.install.sh --dry-run

# 安装并 enable --now（非 root 写入 ~/.config/systemd/user/）
bash scripts/systemd/map-wakers.service.install.sh --project-root "$(pwd)"

# 卸载
bash scripts/systemd/map-wakers.service.install.sh --uninstall
```

Unit 模板：`scripts/systemd/map-wakers.service`（`ExecStart` 指向 `scripts/start-all-wakers.sh`）。

开发机无 systemd 或权限不足时 install 脚本返回非零并打印 stderr，**不会** sudo 重试。

验收：`pytest tests/test_systemd_install.py`
