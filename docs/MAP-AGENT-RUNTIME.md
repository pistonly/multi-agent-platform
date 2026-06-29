# MAP x Agent Runtime Integration

This document describes how MAP should work with external Agent Runtimes such as
Cursor, Claude Code, Codex, or project-specific runners.

## Boundary

MAP owns:

- topic, todo, experiment, review, and audit state
- persona permissions
- CLI and SDK write APIs
- webhook delivery

External Agent Runtime owns:

- reading project skills and repository context
- deciding what reply, summary, or plan text to generate
- returning structured intent to the bridge

The host bridge owns:

- polling `map todos`
- fetching topic snapshots
- building runner context
- local idempotency
- structured logs
- MAP writes through `map --persona host`

Do not embed OpenAI, Anthropic, Cursor, Claude Code, or Codex SDK calls inside
MAP core services.

## Recommended Path: Polling Bridge

Polling is the correctness path. It keeps working when webhook delivery fails or
the IDE is closed.

```bash
map-host-bridge \
  --persona host \
  --interval 30 \
  --agent-runner ./scripts/host-agent-runner \
  --state-file .map/host-bridge-state.json
```

For one-cycle verification:

```bash
map-host-bridge \
  --persona host \
  --once \
  --dry-run \
  --agent-runner ./scripts/host-agent-runner
```

The bridge uses `pending_topic_replies` as the source of truth for reply work.
When `--promote-ready-topics` is enabled, it can also promote ready topics into
experiments after these gates pass:

- `discussion_round == ready`
- `round_summary_count >= 2`
- no pending topic replies for that topic
- no active experiment already exists for the topic

`--promote-ready-topics` is off by default.

## Runner Contract v0

The runner receives one JSON object on stdin and returns one JSON object on
stdout.

### Request

```json
{
  "action": "reply_pending|promote_experiment",
  "topic_id": "uuid",
  "dry_run": false,
  "context": {
    "pending_item": {
      "comment_id": "uuid",
      "excerpt": "..."
    },
    "topic": {
      "id": "uuid",
      "title": "...",
      "discussion_round": "round2",
      "round_summary_count": 1
    },
    "idempotency_key": "topic_id:comment_id"
  }
}
```

### Response

```json
{
  "body": "Markdown reply or experiment plan",
  "parent_id": "uuid-or-null",
  "advance_round": false,
  "create_experiment": false
}
```

Exit codes:

| Code | Meaning | Bridge behavior |
|------|---------|-----------------|
| `0` | Success, stdout is valid JSON | Execute allowed MAP writes |
| `1` | Runner error | Log, write nothing, retry next cycle |
| `2` | Explicit skip | Log and mark local state to avoid noisy retries |

The runner must not write MAP directly. It only returns content and intent. The
bridge performs writes through the host persona.

## Local State

Default state file:

```text
.map/host-bridge-state.json
```

Schema:

```json
{
  "schema_version": 1,
  "topics": {
    "<topic_id>": {
      "last_handled_comment_id": "...",
      "last_round_summary_count": 2,
      "last_action_at": "ISO8601"
    }
  }
}
```

The state file is local runtime data and is ignored by Git.

## Webhook Path

Webhook is an acceleration path, not the correctness path. A receiver can listen
for `topic.comment.created` and start a runner or Agent Runtime session, but it
should still rely on `map todos` and topic snapshots before writing.

Flow:

```text
topic.comment.created
  -> webhook receiver
  -> trigger host bridge or Agent Runtime prompt
  -> map --persona host todos
  -> map --persona host topic show --id <topic>
  -> runner returns content
  -> bridge writes through host persona
```

The existing [WEBHOOK-TOPIC-HOST](./WEBHOOK-TOPIC-HOST.md) guide covers webhook
registration and signature verification.

## Human-In-The-Loop Limits

The bridge may:

- create host replies
- advance topic rounds when a runner explicitly requests it
- create experiments from ready topics when explicitly enabled
- submit newly created experiments for review when explicitly configured

The bridge must not:

- approve experiments without host action
- start experiments without host action
- complete experiments
- bypass reviewer findings
