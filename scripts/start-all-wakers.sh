#!/usr/bin/env bash
# Start host, participant, and reviewer wakers together (default: simple-waker).
#
# Usage:
#   ./scripts/start-all-wakers.sh
#   ./scripts/start-all-wakers.sh --once --dry-run
#   MAP_SIMPLE_ACTIVE_INTERVAL=60 ./scripts/start-all-wakers.sh
#
# Legacy per-item runtime-waker:
#   MAP_USE_LEGACY_WAKER=1 ./scripts/start-all-wakers.sh
#
# Logs: .map/waker-logs/{host,participant,reviewer}.log
# Session prompts: .map/runtime-waker-sessions/<YYYYMMDD-HHMMSS>_<persona>_<session_id>.jsonl
# Stop all with Ctrl+C.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ "${MAP_USE_LEGACY_WAKER:-0}" == "1" ]]; then
  exec "$ROOT/scripts/start-all-wakers-legacy.sh" "$@"
fi

exec "$ROOT/scripts/start-all-simple-wakers.sh" "$@"
