#!/usr/bin/env bash
# Start host, participant, and reviewer simple wakers together.
#
# Usage:
#   ./scripts/start-all-simple-wakers.sh
#   ./scripts/start-all-simple-wakers.sh --once --dry-run
#   ./scripts/start-all-simple-wakers.sh --drain-topics
#
# Logs: .map/waker-logs/{host,participant,reviewer}.log (or MAP_SIMPLE_WAKER_LOG_DIR)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_DIR="${MAP_SIMPLE_WAKER_LOG_DIR:-${MAP_WAKER_LOG_DIR:-.map/waker-logs}}"
mkdir -p "$LOG_DIR"

pids=()
drain_topics=0
drain_timeout="${MAP_WAKER_DRAIN_TIMEOUT_SECONDS:-7200}"
drain_check_interval="${MAP_WAKER_DRAIN_CHECK_INTERVAL_SECONDS:-60}"
extra_args=()

cleanup() {
  local pid
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
}

trap cleanup EXIT INT TERM

usage() {
  cat >&2 <<'EOF'
Usage:
  ./scripts/start-all-simple-wakers.sh [--drain-topics|--until-topics-resolved] [waker args...]

Options:
  --drain-topics, --until-topics-resolved
      Start all persona wakers and stop automatically once `map topic list --status open`
      returns no open topics. Exits 124 if the timeout is reached.

Environment:
  MAP_WAKER_DRAIN_TIMEOUT_SECONDS          Default: 7200
  MAP_WAKER_DRAIN_CHECK_INTERVAL_SECONDS  Default: 60
  MAP_SIMPLE_ACTIVE_INTERVAL               Default passed to start-simple-waker
  MAP_SIMPLE_IDLE_INTERVAL                 Default passed to start-simple-waker
  MAP_SIMPLE_RUNTIME                       Agent runtime: claude (default) or cursor
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --drain-topics|--until-topics-resolved)
      drain_topics=1
      shift
      ;;
    --drain-timeout-seconds)
      if [[ $# -lt 2 ]]; then
        echo "error: --drain-timeout-seconds requires a value" >&2
        exit 2
      fi
      drain_timeout="$2"
      shift 2
      ;;
    --drain-check-interval-seconds)
      if [[ $# -lt 2 ]]; then
        echo "error: --drain-check-interval-seconds requires a value" >&2
        exit 2
      fi
      drain_check_interval="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      extra_args+=("$1")
      shift
      ;;
  esac
done

start_waker() {
  local name="$1"
  shift
  local args=("$@")
  if [[ "$drain_topics" == "1" && "$name" == "host" ]]; then
    args+=(--drain-topics)
  fi
  MAP_SIMPLE_STATE_FILE=".map/simple-waker-state-${name}.json" \
  MAP_SIMPLE_RUNTIME_HOME=".map/claude-runtime-home-${name}" \
  ./scripts/start-simple-waker.sh --persona "$name" "${args[@]}" >>"$LOG_DIR/${name}.log" 2>&1 &
  pids+=("$!")
  echo "started simple waker $name pid=$! log=$LOG_DIR/${name}.log" >&2
}

for persona in host participant reviewer; do
  if ! map --persona "$persona" persona whoami >/dev/null 2>&1; then
    echo "error: map --persona $persona persona whoami failed (check .map/ and MAP API)" >&2
    exit 1
  fi
done

start_waker host "${extra_args[@]}"
start_waker participant "${extra_args[@]}"
start_waker reviewer "${extra_args[@]}"

echo "" >&2
echo "All simple wakers running. Tail logs:" >&2
echo "  tail -f $LOG_DIR/host.log $LOG_DIR/participant.log $LOG_DIR/reviewer.log" >&2
if [[ "$drain_topics" == "1" ]]; then
  echo "Topic drain mode: waiting until all open topics are resolved/closed." >&2
  echo "Timeout: ${drain_timeout}s; check interval: ${drain_check_interval}s" >&2
else
  echo "Press Ctrl+C to stop all." >&2
fi

count_open_topics() {
  python3 - "$ROOT" <<'PY'
import subprocess
import sys
import yaml

root = sys.argv[1]
page = 1
page_size = 100
total = 0
while True:
    cmd = [
        "map",
        "--persona",
        "host",
        "--project-root",
        root,
        "topic",
        "list",
        "--status",
        "open",
        "--page",
        str(page),
        "--page-size",
        str(page_size),
    ]
    result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        print(result.stderr.strip() or result.stdout.strip(), file=sys.stderr)
        sys.exit(result.returncode)
    payload = yaml.safe_load(result.stdout) if result.stdout.strip() else []
    if payload is None:
        payload = []
    if not isinstance(payload, list):
        print(f"unexpected topic list payload: {type(payload).__name__}", file=sys.stderr)
        sys.exit(2)
    total += len(payload)
    if len(payload) < page_size:
        break
    page += 1
print(total)
PY
}

if [[ "$drain_topics" == "1" ]]; then
  start_ts="$(date +%s)"
  while true; do
    now_ts="$(date +%s)"
    elapsed=$((now_ts - start_ts))
    if (( elapsed >= drain_timeout )); then
      remaining="$(count_open_topics || echo unknown)"
      echo "error: topic drain timeout after ${elapsed}s; remaining open topics=${remaining}" >&2
      exit 124
    fi
    remaining="$(count_open_topics)"
    echo "topic drain check: remaining_open_topics=${remaining} elapsed=${elapsed}s" >&2
    if [[ "$remaining" == "0" ]]; then
      echo "All open topics resolved/closed. Stopping wakers." >&2
      exit 0
    fi
    sleep "$drain_check_interval"
  done
else
  wait
fi
