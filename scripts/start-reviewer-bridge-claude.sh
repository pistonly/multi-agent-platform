#!/usr/bin/env bash
# Poll pending_reviews and submit experiment reviews via reviewer persona + Claude runner.
#
# Usage:
#   ./scripts/start-reviewer-bridge-claude.sh
#   ./scripts/start-reviewer-bridge-claude.sh --once --dry-run
#
# Environment (optional):
#   MAP_REVIEWER_PERSONA          default: reviewer
#   MAP_REVIEWER_INTERVAL         default: 30
#   MAP_REVIEWER_RUNNER_TIMEOUT   default: 180
#   MAP_REVIEWER_STATE_FILE       default: .map/reviewer-bridge-state.json
#   MAP_REVIEWER_RUNNER           default: python3 scripts/claude-reviewer-runner.py
#   MAP_REVIEWER_MAX_REVIEWS      default: 1
#   MAP_REVIEWER_REVIEW_DIR       default: .map/generated-reviews
#
# Claude credentials (resolved by the runner itself):
#   ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN
#   ANTHROPIC_BASE_URL              optional, for Anthropic-compatible endpoints
#   ANTHROPIC_MODEL / CLAUDE_MODEL  optional model override

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PERSONA="${MAP_REVIEWER_PERSONA:-reviewer}"
INTERVAL="${MAP_REVIEWER_INTERVAL:-30}"
RUNNER_TIMEOUT="${MAP_REVIEWER_RUNNER_TIMEOUT:-180}"
STATE_FILE="${MAP_REVIEWER_STATE_FILE:-.map/reviewer-bridge-state.json}"
RUNNER="${MAP_REVIEWER_RUNNER:-python3 scripts/claude-reviewer-runner.py}"
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

echo "Starting MAP reviewer bridge (claude backend, persona=$PERSONA interval=${INTERVAL}s)" >&2
exec "${cmd[@]}"
