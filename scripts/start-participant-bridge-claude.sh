#!/usr/bin/env bash
# Poll open MAP topics and participate via participant persona + Claude runner.
#
# Usage:
#   ./scripts/start-participant-bridge-claude.sh
#   ./scripts/start-participant-bridge-claude.sh --once --dry-run
#
# Environment (optional):
#   MAP_PARTICIPANT_PERSONA         default: participant
#   MAP_PARTICIPANT_INTERVAL        default: 30
#   MAP_PARTICIPANT_RUNNER_TIMEOUT  default: 180
#   MAP_PARTICIPANT_STATE_FILE      default: .map/participant-bridge-state.json
#   MAP_PARTICIPANT_RUNNER          default: python3 scripts/claude-participant-runner.py
#   MAP_PARTICIPANT_MAX_ACTIONS     default: 1 (comments per cycle)
#
# Claude credentials (resolved by the runner itself):
#   ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN
#   ANTHROPIC_BASE_URL              optional, for Anthropic-compatible endpoints
#   ANTHROPIC_MODEL / CLAUDE_MODEL  optional model override

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PERSONA="${MAP_PARTICIPANT_PERSONA:-participant}"
INTERVAL="${MAP_PARTICIPANT_INTERVAL:-30}"
RUNNER_TIMEOUT="${MAP_PARTICIPANT_RUNNER_TIMEOUT:-180}"
STATE_FILE="${MAP_PARTICIPANT_STATE_FILE:-.map/participant-bridge-state.json}"
RUNNER="${MAP_PARTICIPANT_RUNNER:-python3 scripts/claude-participant-runner.py}"
MAX_ACTIONS="${MAP_PARTICIPANT_MAX_ACTIONS:-1}"

if ! map --persona "$PERSONA" persona whoami >/dev/null 2>&1; then
  echo "error: map --persona $PERSONA persona whoami failed" >&2
  exit 1
fi

cmd=(python3 -m cli.participant_worker
  --persona "$PERSONA"
  --interval "$INTERVAL"
  --max-actions-per-cycle "$MAX_ACTIONS"
  --agent-runner "$RUNNER"
  --state-file "$STATE_FILE"
  --runner-timeout "$RUNNER_TIMEOUT"
)

if [[ $# -gt 0 ]]; then
  cmd+=("$@")
fi

echo "Starting MAP participant bridge (claude backend, persona=$PERSONA interval=${INTERVAL}s max_actions=$MAX_ACTIONS)" >&2
exec "${cmd[@]}"
