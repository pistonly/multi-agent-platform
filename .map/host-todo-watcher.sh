#!/usr/bin/env bash
# Host todo watcher: poll every 30s, wake on obligation todo changes, 5m heartbeat otherwise.
set -euo pipefail

ROOT="${MAP_PROJECT_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$ROOT"

POLL_INTERVAL="${HOST_TODO_POLL_INTERVAL:-30}"
HEARTBEAT_INTERVAL="${HOST_TODO_HEARTBEAT_INTERVAL:-300}"
PROMPT="${HOST_TODO_PROMPT:-以 host 身份执行 map work，处理所有待办}"

fingerprint() {
  map --persona host work 2>/dev/null | python3 -c "
import json, sys
try:
    import yaml
except ImportError:
    sys.exit(2)
raw = sys.stdin.read()
if not raw.strip():
    sys.exit(1)
data = yaml.safe_load(raw) or {}
todos = data.get('todos') or {}
keys = (
    'pending_topic_replies',
    'pending_advance_rounds',
    'pending_round_acks',
    'pending_replies',
    'pending_plan_revisions',
    'pending_reviews',
    'pending_result_reviews',
    'mentions',
    'action_items',
    'my_open_experiments',
)
fp = {k: todos.get(k) or [] for k in keys}
notifs = data.get('notifications') or {}
fp['_unread_notifications'] = notifs.get('unread_count', 0)
print(json.dumps(fp, sort_keys=True, default=str))
"
}

LAST_FP=""
LAST_HEARTBEAT=$(date +%s)

while true; do
  sleep "$POLL_INTERVAL"
  NOW=$(date +%s)

  if ! FP="$(fingerprint)"; then
    echo "AGENT_LOOP_WAKE_host_todos {\"prompt\":\"$PROMPT\",\"reason\":\"map_work_error\"}" >&2
    continue
  fi

  if [[ -n "$LAST_FP" && "$FP" != "$LAST_FP" ]]; then
    echo "AGENT_LOOP_WAKE_host_todos {\"prompt\":\"$PROMPT\",\"reason\":\"todo_changed\"}"
    LAST_HEARTBEAT=$NOW
  elif (( NOW - LAST_HEARTBEAT >= HEARTBEAT_INTERVAL )); then
    echo "AGENT_LOOP_WAKE_host_todos {\"prompt\":\"$PROMPT\",\"reason\":\"heartbeat\"}"
    LAST_HEARTBEAT=$NOW
  fi

  LAST_FP="$FP"
done
