# v0.7 P2: Host worker bridge mode + Agent Runtime integration

## Source Topic

- topic_id: `ca73366d-ce06-426f-9a12-956a0ce5bdf6`
- title: `v0.7 P2：Host worker 常驻化与 Agent Runtime 自动监控`
- discussion_round: `ready`
- round_summary_count: `2`

## Background

v0.7 P1 has added topic discussion state (`discussion_round`,
`round_summary_count`) and the `advance-round` API/CLI. The current
`cli/host_worker.py` can poll `pending_topic_replies` and optionally promote
ready topics, but it still behaves like a template-based polling helper.

The topic discussion converged on a thinner design:

- MAP owns state, permissions, audit, CLI, webhook, and lifecycle APIs.
- External Agent Runtime owns LLM reasoning and content generation.
- The host-side worker/bridge owns polling, context assembly, idempotency,
  runner invocation, structured logging, and host persona writes.

## Goals

1. Add a bridge path for host automation without embedding LLM SDKs in MAP.
2. Define and implement the `--agent-runner` contract.
3. Add local idempotency state for retries and restarts.
4. Preserve the current human-in-the-loop lifecycle boundary.
5. Document MAP x Agent Runtime integration paths.

## Scope

### Required

- Implement `map-host-bridge` or refactor `cli/host_worker.py` into bridge mode.
- Add `--agent-runner <cmd>` support:
  - stdin: one-line JSON request.
  - stdout: one-line JSON response.
  - exit code `0`: valid response, bridge may write MAP.
  - exit code `1`: runner error, bridge logs and does not write MAP.
  - exit code `2`: explicit skip, bridge logs and may mark state as skipped.
- Add `--runner-timeout`, default `120s`.
- Add local state file `.map/host-bridge-state.json` with `schema_version: 1`.
- Ensure `--dry-run` covers runner invocation, MAP writes, and promote actions.
- Keep all MAP writes through `map --persona host`.
- Keep `--promote-ready-topics` default off.
- Add focused tests for runner contract, dry-run, idempotency, and auto-promote guardrails.
- Add `docs/MAP-AGENT-RUNTIME.md` covering bridge mode, webhook receiver, and Agent Runtime wiring.

### Spike / Optional

- Minimal webhook receiver stub for `topic.comment.created`.
- Cursor SDK or equivalent Agent Runtime dogfood note.
- Docker Compose sidecar example for the bridge container.

## Runner Contract v0

### stdin JSON

```json
{
  "action": "reply_pending|round_summary|promote_experiment",
  "topic_id": "uuid",
  "dry_run": false,
  "context": {
    "pending_item": {
      "comment_id": "uuid",
      "excerpt": "..."
    },
    "topic": {
      "discussion_round": "round2",
      "round_summary_count": 1
    },
    "idempotency_key": "topic_id:comment_id"
  }
}
```

### stdout JSON

```json
{
  "body": "Markdown reply or summary",
  "parent_id": "uuid-or-null",
  "advance_round": false,
  "create_experiment": false
}
```

The runner must not write MAP directly. The bridge parses stdout and performs
allowed writes through the host persona.

## Idempotency State

Path: `.map/host-bridge-state.json`

```json
{
  "schema_version": 1,
  "topics": {
    "<topic_id>": {
      "last_handled_comment_id": "...",
      "last_round_summary_count": 1,
      "last_action_at": "ISO8601"
    }
  }
}
```

P2 does not add a service-side lease. That can be revisited in v0.8 if multiple
bridge instances become a supported deployment mode.

## Guardrails

- Do not embed OpenAI, Anthropic, Cursor, Claude Code, or Codex SDK calls inside
  MAP core services.
- Do not let the runner hold MAP write authority.
- Do not auto-approve, auto-start, or auto-complete experiments.
- `auto-promote` only runs when explicitly enabled and:
  - `discussion_round == ready`
  - `round_summary_count >= 2`
  - no `pending_topic_replies`
  - no active experiment already exists for the topic
- Webhook is an acceleration path; polling `map todos` remains the correctness
  path.

## Acceptance Criteria

1. `ruff check server cli sdk tests` passes.
2. `pytest -q` passes.
3. `npm run build` still passes if Web or shared types are touched.
4. `map-host-bridge` or host worker bridge mode can run one dry-run cycle and
   print intended actions without MAP writes.
5. A fake runner test proves:
   - exit `0` writes the expected host comment/action.
   - exit `1` logs and writes nothing.
   - exit `2` skips without repeated noisy retries.
6. Restarting with the same `.map/host-bridge-state.json` does not duplicate
   replies, round advancement, or experiment creation.
7. Documentation explains:
   - polling path,
   - webhook receiver path,
   - Agent Runtime responsibilities,
   - host persona write boundary.

## Out of Scope

- Production-grade distributed lease.
- Direct LLM SDK integration in MAP.
- Automatic experiment approval/start/completion.
- Full Cursor SDK production integration as a merge blocker.
