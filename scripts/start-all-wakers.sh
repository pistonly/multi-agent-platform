#!/usr/bin/env bash
# Start host, participant, and reviewer runtime wakers together.
#
# Usage:
#   ./scripts/start-all-wakers.sh
#   ./scripts/start-all-wakers.sh --once --dry-run
#   MAP_RUNTIME_INTERVAL=60 ./scripts/start-all-wakers.sh
#
# Logs: .map/waker-logs/{host,participant,reviewer}.log
# Session prompts: .map/runtime-waker-sessions/<session_id>.jsonl
# Stop all with Ctrl+C.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_DIR="${MAP_WAKER_LOG_DIR:-.map/waker-logs}"
mkdir -p "$LOG_DIR"

pids=()

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

start_waker() {
  local name="$1"
  shift
  MAP_RUNTIME_STATE_FILE="${MAP_RUNTIME_STATE_FILE:-.map/runtime-waker-state-${name}.json}" \
  MAP_RUNTIME_HOME="${MAP_RUNTIME_HOME:-.map/claude-runtime-home-${name}}" \
  ./scripts/start-runtime-waker.sh --persona "$name" "$@" >>"$LOG_DIR/${name}.log" 2>&1 &
  pids+=("$!")
  echo "started waker $name pid=$! log=$LOG_DIR/${name}.log state=.map/runtime-waker-state-${name}.json" >&2
}

for persona in host participant reviewer; do
  if ! map --persona "$persona" persona whoami >/dev/null 2>&1; then
    echo "error: map --persona $persona persona whoami failed (check .map/ and MAP API)" >&2
    exit 1
  fi
done

extra_args=("$@")
start_waker host "${extra_args[@]}"
start_waker participant "${extra_args[@]}"
start_waker reviewer "${extra_args[@]}"

echo "" >&2
echo "All runtime wakers running. Tail logs:" >&2
echo "  tail -f $LOG_DIR/host.log $LOG_DIR/participant.log $LOG_DIR/reviewer.log" >&2
echo "Session wake logs: .map/runtime-waker-sessions/" >&2
echo "Press Ctrl+C to stop all." >&2

wait
