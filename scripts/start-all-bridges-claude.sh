#!/usr/bin/env bash
# Start host, participant, and reviewer bridges together, all backed by the
# Claude Agent SDK runners.
#
# Usage:
#   ./scripts/start-all-bridges-claude.sh
#   MAP_HOST_SUBMIT_REVIEW=1 ./scripts/start-all-bridges-claude.sh
#   ./scripts/start-all-bridges-claude.sh --once --dry-run
#
# Logs: .map/bridge-logs/{host,participant,reviewer}.log
# Stop all with Ctrl+C.
#
# Each MAP_*_RUNNER env var still wins if set, so this script is equivalent
# to exporting MAP_HOST_RUNNER=python3 scripts/claude-host-runner.py and the
# participant/reviewer equivalents before invoking the per-bridge scripts.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export MAP_HOST_RUNNER="${MAP_HOST_RUNNER:-python3 scripts/claude-host-runner.py}"
export MAP_PARTICIPANT_RUNNER="${MAP_PARTICIPANT_RUNNER:-python3 scripts/claude-participant-runner.py}"
export MAP_REVIEWER_RUNNER="${MAP_REVIEWER_RUNNER:-python3 scripts/claude-reviewer-runner.py}"

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
  local runner_var="MAP_${name^^}_RUNNER"
  local runner="${!runner_var:-default}"
  shift
  "$@" >>"$LOG_DIR/${name}.log" 2>&1 &
  pids+=("$!")
  echo "started $name pid=$! log=$LOG_DIR/${name}.log runner=$runner" >&2
}

start_bridge host ./scripts/start-host-bridge-claude.sh
start_bridge participant ./scripts/start-participant-bridge-claude.sh
start_bridge reviewer ./scripts/start-reviewer-bridge-claude.sh

echo "" >&2
echo "All Claude-backed bridges running. Tail logs:" >&2
echo "  tail -f $LOG_DIR/host.log $LOG_DIR/participant.log $LOG_DIR/reviewer.log" >&2
echo "Press Ctrl+C to stop all." >&2

wait
