#!/usr/bin/env bash
# Start host, participant, and reviewer simple wakers together.
#
# Usage:
#   ./scripts/start-all-simple-wakers.sh
#   ./scripts/start-all-simple-wakers.sh --once --dry-run
#
# Logs: .map/simple-waker-logs/{host,participant,reviewer}.log

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_DIR="${MAP_SIMPLE_WAKER_LOG_DIR:-.map/simple-waker-logs}"
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
  MAP_SIMPLE_STATE_FILE=".map/simple-waker-state-${name}.json" \
  MAP_SIMPLE_RUNTIME_HOME=".map/claude-runtime-home-${name}" \
  ./scripts/start-simple-waker.sh --persona "$name" "$@" >>"$LOG_DIR/${name}.log" 2>&1 &
  pids+=("$!")
  echo "started simple waker $name pid=$! log=$LOG_DIR/${name}.log" >&2
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
echo "All simple wakers running. Tail logs:" >&2
echo "  tail -f $LOG_DIR/host.log $LOG_DIR/participant.log $LOG_DIR/reviewer.log" >&2
echo "Press Ctrl+C to stop all." >&2

wait
