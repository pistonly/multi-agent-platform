#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

stop_bridges() {
  pkill -f "cli.host_worker" 2>/dev/null || true
  pkill -f "cli.participant_worker" 2>/dev/null || true
  pkill -f "cli.reviewer_worker" 2>/dev/null || true
  sleep 2
}

run_cycle() {
  local label="$1"
  echo "=== $label $(date -Iseconds) ==="
  ./scripts/start-all-bridges.sh >> .map/bridge-logs/idempotency-test.log 2>&1 &
  local bpid=$!
  echo "started pid=$bpid"
  sleep 95
  kill "$bpid" 2>/dev/null || true
  wait "$bpid" 2>/dev/null || true
  stop_bridges
  echo "=== $label done ==="
}

stop_bridges
run_cycle "cycle-1"
run_cycle "restart-cycle"
