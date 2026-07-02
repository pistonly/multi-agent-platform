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
| `MAP_RUNTIME_INTERVAL` | `30` | Polling interval |
| `MAP_RUNTIME_STATE_FILE` | `.map/runtime-waker-state.json` | Runtime session + event state |
| `MAP_RUNTIME_HOME` | backend-specific | Runtime home passed to Claude or Codex (ignored by `cursor`) |
| `MAP_RUNTIME_BACKEND` | `claude` | Runtime backend: `claude`, `codex`, or `cursor` |
| `MAP_RUNTIME_MAX_WAKES_PER_CYCLE` | `3` | Hard cap per cycle |
| `MAP_RUNTIME_COOLDOWN_SECONDS` | `300` | Retry cooldown for failed events |
| `MAP_RUNTIME_FORCE` | `0` | Re-wake already seen events |
| `MAP_RUNTIME_MODEL` | unset | Optional runtime model override |
| `MAP_RUNTIME_CODEX_BIN` | unset | Optional Codex binary path for `codex` backend |

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

The state file stores the active resumed session per persona and wake context.
When the wake context changes, for example from one topic to another topic or
from one experiment to another experiment, the waker clears the old session id
and starts a fresh runtime session. Events in the same context continue to
resume the existing session.

```json
{
  "schema_version": 1,
  "personas": {
    "host": {
      "runtime_session_id": "...",
      "last_wake_context_key": "topic:<topic_id>",
      "last_wake_object_id": "<topic_id>",
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

**Claude backend only:** each Claude SDK `session_id` gets an append-only JSONL
file under `.map/runtime-waker-sessions/`. New sessions use a sortable filename:

`YYYYMMDD-HHMMSS_<persona>_<session_id>.jsonl` (timestamps default to
`Asia/Shanghai`; override with `MAP_LOG_TIMEZONE`). Resume wakes append to the
same file. Legacy plain `<session_id>.jsonl` files are still read if present.

Every `PersonaAgentClient` wake records:

- full user `prompt`
- first 200 characters of assistant text (`response_preview`)
- `status`, `persona`, `integration`, timestamp

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
