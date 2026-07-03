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

## Phase 2 SSE overlay rollout checklist (v0.8)

Phase 2 实验 `41687a01-3992-471b-b415-8ad80f732f80` 已完成；A1a / A1b / A1总 / A2 / A3 / A4 / A5 / A6 / A7 全部通过 reviewer 红线。完整 baseline 见 `.map/generated-plans/phase2-p95-baseline.json`。

### 验收摘要（reviewer 红线 vs 实测）

| 红线 | 出处 | 实测 | 余量 |
|------|------|------|------|
| SSE 帧传输 < 1s（A1a 硬门槛） | reviewer 红线 | mean 0.5ms, p95 0.5ms, max 0.5ms | 2000× |
| `mention` 端到端 P95 < 5s | reviewer 立场 `bf3f263d` + 共识 7 | 2105.2ms（20 trials）| 58% |
| `pending_review` 端到端 P95 < 10s | 同上 | 2105.0ms | 79% |
| `topic_lifecycle` 端到端 P95 < 30s | 同上 | 2105.1ms | 93% |
| 漏事件率 = 0（A2） | reviewer 红线 | 3/3 + 10/10 + 30/30 三档全部补漏 | n/a |
| 空轮询比例稳态期 ≥ 95%（A3） | reviewer 红线 | 档 b 100% / 档 a 90%（受控流量段不套红线）| 满足 |
| 重复唤醒率 < 0.1%（A4） | reviewer 红线 | 5 场景全 ≤ 1 wake | 满足 |
| 幂等写成功率 100%（A5） | reviewer 红线 | 7 场景全 ≤ 1 wake；server UNIQUE 409 路径 0 wake | 满足 |
| 审计三段 join（A6） | Phase 1 继承 | SSE 路径 3-way join 通过 + Phase 1 5 case 无回归 | 满足 |
| sessions jsonl `event_source` ⊆ {polling, sse, replay} 且 ≥ 2 种（A7） | reviewer 红线 | 三值全部覆盖 + replay 必填 | 满足 |

### 上线 checklist

在生产 / 准生产环境启用 SSE 主路径前，按此清单逐项验证：

| 步骤 | 命令 / 操作 | 通过判据 |
|------|------------|---------|
| 1. 拉取 Phase 2 commits | `git log --oneline aa49785 -- 12` | HEAD 含 `580713c` (Phase 2 源码) + `aa49785` (A6+A7 测试) 等 |
| 2. 跑回归 | `pytest tests/test_waker_phase1_acceptance.py tests/test_waker_phase2_*.py -q` | 全部 111 测试绿 |
| 3. 跑 lint | `ruff check cli/runtime_waker.py tests/test_waker_phase2_*.py` | clean |
| 4. dry-run 启动 waker | `./scripts/start-all-wakers.sh --dry-run` | 三 persona 都正确 `_sse_consume_stream` 启动 + polling 兜底在 |
| 5. 注入合成 notification 验证 SSE 主路径 | `curl -X POST http://localhost:8001/api/v1/topics/<id>/comments` 触发 host notification | 5s 内收到 wake（mention < 5s 红线） |
| 6. 验证 D3 重连补偿 | kill docker API → 30s 内恢复 | sessions jsonl 出现 `event_source="replay"`，无 wake 丢失 |
| 7. 验证兜底轮询保留 | `MAP_RUNTIME_SSE_ENABLED=0 ./scripts/start-all-wakers.sh` | polling 主路径仍工作（escape hatch） |
| 8. 监控基线 | 检查 `.map/runtime-waker-state-*.json` 中 `sse_events_received` / `sse_reconnect_total` / `sse_replay_runs` | 24h 后无异常堆积 |
| 9. 回滚预案 | `MAP_RUNTIME_SSE_ENABLED=0` 环境变量回退到纯轮询 | 立即生效，无需改代码 |

### 关键数字与口径说明

- **A1 拆分纪律**（回应 U5）：`A1总 = A1a_SSE + A1b_waker_overhead + A1b_CLI_startup`。本机实测 SSE 帧 ~0.5ms，waker overhead 2.1ms，Claude CLI 启动 ~2.1s。三段独立测量，瓶颈归属清晰。
- **Phase 1 P95 baseline ≠ Phase 2 P95 baseline**：Phase 1 测的是 `inbound_event` UNIQUE 主闸 DB write 时间（~0.85ms）；Phase 2 测的是端到端 notification → wake_resume 时间（~2.1s）。两者口径不同，无法直接比较。
- **A1b 无准入门槛**：warm-pool 优化归 v0.8 backlog；当前 2.1s 已让 A1 总 P95 落在 reviewer 红线 7–42% 利用率区间。
- **D4 补漏豁免机制**：仅 `event_source="replay"` 路径豁免 60s 客户端限速；服务端 `inbound_event.UNIQUE(fingerprint)` 主闸仍生效防跨进程重投。
- **D3 重连退避**：1s → 2s → 4s → 8s → 16s → 30s（上限）+ jitter；退避状态持久化到 `.map/runtime-waker-state-*.json`，进程重启不丢位置。

### 已知边界 / v0.8 backlog

| 项 | 描述 | 优先级 |
|----|------|--------|
| Warm-pool 复用 Claude session | 把 A1b 从 ~2.1s 降到 ~500ms | P1（reviewer 提及） |
| `inbound_event` 表 TTL / 分区 | Phase 2 SSE 触发更频繁，按 D1 写入 `source="sse"` 会比 Phase 1 多 ~10× | P2 |
| 真 e2e triple（真 SSE + 真 backend）| 当前 A1总用 stub backend 校准到 A1b Test 1 实测值；docker harness 内可加真链路验证 | P3 |
| Cursor backend 非 dry-run 实跑 | 仍需 `CURSOR_API_KEY` | P3 |

### Reviewer 提请评审项

I6 提交后，reviewer 应在 result_review 阶段确认：

1. **A1 红线出处可追溯**（U1 回应）：plan v2 §A1总 + §硬性约束显式声明红线 = reviewer 立场 `bf3f263d` + Round 1 Summary `3f9d80cd` 共识 7。
2. **D4 补漏豁免无滥用**（U2 回应）：仅 `event_source="replay"` 路径豁免；服务端 UNIQUE 主闸不受影响（见 A5 测试 3/4）。
3. **A3 双档 + 边界分段**（U3 + U4 回应）：档 b 100% / 档 a 90%（受控段不套红线）；recovery 期不计分母；三段报表启动 / 稳态 / 恢复期 / post-recovery 完整。
4. **A1 拆分 A1a + A1b**（U5 回应）：A1a 传输硬门槛 < 1s 通过；A1b CLI 启动 ~2.1s 观测项无门槛（warm-pool 优化归 v0.8）。
5. **B 实验未 commit 代码的根因**（reviewer process bug）：见 I2+I3 source commit log §风险与告知；本次实验 commit 节奏与 B 不同，但 result_review 时需 reviewer 知悉。
