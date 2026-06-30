#!/usr/bin/env bash
# Start host, participant, and reviewer bridges together, all backed by the
# in-process Claude SDK client with session resume.
#
# As of v0.7 P4 there is no per-action subprocess runner; each bridge holds
# one ClaudeSDKClient for its lifetime. ``claude_session_id`` is persisted
# in the bridge state file so the next restart resumes the same session.
#
# Usage:
#   ./scripts/start-all-bridges-claude.sh
#   ./scripts/start-all-bridges-claude.sh --once --dry-run
#
# Logs: .map/bridge-logs/{host,participant,reviewer}.log
# Stop all with Ctrl+C.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_DIR="${MAP_BRIDGE_LOG_DIR:-.map/bridge-logs}"
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

start_bridge() {
  local name="$1"
  shift
  "$@" >>"$LOG_DIR/${name}.log" 2>&1 &
  pids+=("$!")
  echo "started $name pid=$! log=$LOG_DIR/${name}.log" >&2
}

start_bridge host ./scripts/start-host-bridge-claude.sh
start_bridge participant ./scripts/start-participant-bridge-claude.sh
start_bridge reviewer ./scripts/start-reviewer-bridge-claude.sh

echo "" >&2
echo "All Claude-backed bridges running. Tail logs:" >&2
echo "  tail -f $LOG_DIR/host.log $LOG_DIR/participant.log $LOG_DIR/reviewer.log" >&2
echo "Press Ctrl+C to stop all." >&2

wait
