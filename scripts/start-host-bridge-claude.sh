#!/usr/bin/env bash
# Start MAP host bridge using the Claude Agent SDK runner.
#
# Usage:
#   ./scripts/start-host-bridge-claude.sh
#   MAP_HOST_INTERVAL=60 ./scripts/start-host-bridge-claude.sh
#   ./scripts/start-host-bridge-claude.sh --once --dry-run
#
# Environment (optional):
#   MAP_HOST_PERSONA          default: host
#   MAP_HOST_INTERVAL         default: 30 (seconds)
#   MAP_HOST_RUNNER_TIMEOUT   default: 180 (seconds)
#   MAP_HOST_STATE_FILE       default: .map/host-bridge-state.json
#   MAP_HOST_RUNNER           default: python3 scripts/claude-host-runner.py
#   MAP_HOST_NO_LIFECYCLE     set to 1 to disable summary/advance/promote (reply-only mode)
#   MAP_HOST_SUBMIT_REVIEW    set to 1 to --submit-for-review on new experiments
#   MAP_HOST_PLAN_DIR         plan output dir when promoting (default: .map/generated-plans)
#
# Claude credentials (resolved by the runner itself):
#   ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN
#   ANTHROPIC_BASE_URL        optional, for Anthropic-compatible endpoints
#   ANTHROPIC_MODEL / CLAUDE_MODEL   optional model override
#
# Requires: MAP API running, .map/ bootstrapped, claude-agent-sdk installed,
# and either the `claude` CLI on PATH or claude-agent-sdk able to locate it.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PERSONA="${MAP_HOST_PERSONA:-host}"
INTERVAL="${MAP_HOST_INTERVAL:-30}"
RUNNER_TIMEOUT="${MAP_HOST_RUNNER_TIMEOUT:-180}"
STATE_FILE="${MAP_HOST_STATE_FILE:-.map/host-bridge-state.json}"
RUNNER="${MAP_HOST_RUNNER:-python3 scripts/claude-host-runner.py}"
PLAN_DIR="${MAP_HOST_PLAN_DIR:-.map/generated-plans}"

if ! map --persona "$PERSONA" persona whoami >/dev/null 2>&1; then
  echo "error: map --persona $PERSONA persona whoami failed (check .map/ and MAP API)" >&2
  exit 1
fi

map_cmd=(python3 -m cli.host_worker
  --persona "$PERSONA"
  --interval "$INTERVAL"
  --agent-runner "$RUNNER"
  --state-file "$STATE_FILE"
  --runner-timeout "$RUNNER_TIMEOUT"
  --plan-dir "$PLAN_DIR"
)

if [[ "${MAP_HOST_NO_LIFECYCLE:-0}" == "1" ]]; then
  map_cmd+=(--no-manage-topic-lifecycle)
else
  map_cmd+=(--manage-topic-lifecycle)
fi

if [[ "${MAP_HOST_SUBMIT_REVIEW:-0}" == "1" ]]; then
  map_cmd+=(--submit-for-review)
fi

if [[ $# -gt 0 ]]; then
  map_cmd+=("$@")
fi

echo "Starting MAP host bridge (claude backend, persona=$PERSONA interval=${INTERVAL}s runner=$RUNNER lifecycle=$([[ "${MAP_HOST_NO_LIFECYCLE:-0}" == "1" ]] && echo off || echo on))" >&2
echo "Stop with Ctrl+C" >&2
exec "${map_cmd[@]}"
