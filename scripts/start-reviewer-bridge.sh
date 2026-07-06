#!/usr/bin/env bash
# DEPRECATED: Use ./scripts/start-all-wakers.sh (simple-waker) + reviewer Skill instead.
# See docs/LEGACY-ENTRY-MATRIX.md. Phase 1: retained with deprecation label only.
#
# Poll pending_reviews and submit experiment reviews via reviewer persona + Cursor runner.
#
# Usage:
#   ./scripts/start-reviewer-bridge.sh
#   ./scripts/start-reviewer-bridge.sh --once --dry-run

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PERSONA="${MAP_REVIEWER_PERSONA:-reviewer}"
INTERVAL="${MAP_REVIEWER_INTERVAL:-30}"
RUNNER_TIMEOUT="${MAP_REVIEWER_RUNNER_TIMEOUT:-180}"
STATE_FILE="${MAP_REVIEWER_STATE_FILE:-.map/reviewer-bridge-state.json}"
RUNNER="${MAP_REVIEWER_RUNNER:-python3 scripts/cursor-reviewer-runner.py}"
MAX_REVIEWS="${MAP_REVIEWER_MAX_REVIEWS:-1}"
REVIEW_DIR="${MAP_REVIEWER_REVIEW_DIR:-.map/generated-reviews}"

if ! map --persona "$PERSONA" persona whoami >/dev/null 2>&1; then
  echo "error: map --persona $PERSONA persona whoami failed" >&2
  exit 1
fi

cmd=(python3 -m cli.reviewer_worker
  --persona "$PERSONA"
  --interval "$INTERVAL"
  --max-reviews-per-cycle "$MAX_REVIEWS"
  --agent-runner "$RUNNER"
  --state-file "$STATE_FILE"
  --review-dir "$REVIEW_DIR"
  --runner-timeout "$RUNNER_TIMEOUT"
)

if [[ $# -gt 0 ]]; then
  cmd+=("$@")
fi

echo "Starting MAP reviewer bridge (persona=$PERSONA interval=${INTERVAL}s)" >&2
exec "${cmd[@]}"
