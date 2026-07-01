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
