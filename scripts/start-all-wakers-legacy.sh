#!/usr/bin/env bash
# Legacy: per-item runtime-waker (SSE + fingerprints) for all personas.
#
# Prefer ./scripts/start-all-wakers.sh (simple-waker) unless debugging v0.8/v0.9 waker.

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
  local inflight="$2"
  shift 2
  MAP_RUNTIME_STATE_FILE="${MAP_RUNTIME_STATE_FILE:-.map/runtime-waker-state-${name}.json}" \
  MAP_RUNTIME_HOME="${MAP_RUNTIME_HOME:-.map/claude-runtime-home-${name}}" \
  MAP_RUNTIME_PERSONA_INFLIGHT_SECONDS="$inflight" \
  ./scripts/start-runtime-waker-claude.sh --persona "$name" "$@" >>"$LOG_DIR/${name}.log" 2>&1 &
  pids+=("$!")
  echo "started legacy waker $name pid=$! log=$LOG_DIR/${name}.log state=.map/runtime-waker-state-${name}.json inflight=${inflight}s" >&2
}

for persona in host participant reviewer; do
  if ! map --persona "$persona" persona whoami >/dev/null 2>&1; then
    echo "error: map --persona $persona persona whoami failed (check .map/ and MAP API)" >&2
    exit 1
  fi
done

extra_args=("$@")
host_inflight="${MAP_RUNTIME_HOST_INFLIGHT_SECONDS:-600}"
participant_inflight="${MAP_RUNTIME_PARTICIPANT_INFLIGHT_SECONDS:-300}"
reviewer_inflight="${MAP_RUNTIME_REVIEWER_INFLIGHT_SECONDS:-300}"
start_waker host "$host_inflight" "${extra_args[@]}"
start_waker participant "$participant_inflight" "${extra_args[@]}"
start_waker reviewer "$reviewer_inflight" "${extra_args[@]}"

echo "" >&2
echo "All legacy runtime wakers running. Tail logs:" >&2
echo "  tail -f $LOG_DIR/host.log $LOG_DIR/participant.log $LOG_DIR/reviewer.log" >&2
echo "Session wake logs: .map/runtime-waker-sessions/" >&2
echo "Press Ctrl+C to stop all." >&2

wait
