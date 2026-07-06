#!/usr/bin/env bash
# DEPRECATED: Use ./scripts/start-all-wakers.sh (simple-waker) + participant Skill instead.
# See docs/LEGACY-ENTRY-MATRIX.md. Phase 1: retained with deprecation label only.
#
# Poll open MAP topics and participate via participant persona + in-process Claude SDK.
#
# As of v0.7 P4 the participant bridge holds a persistent Claude SDK session
# with resume; no per-action subprocess runner is spawned. Claude decides
# what to comment / when to wait for host Round Summary by invoking skills
# from .cursor/skills/.
#
# Usage:
#   ./scripts/start-participant-bridge-claude.sh
#   ./scripts/start-participant-bridge-claude.sh --once --dry-run
#
# Environment (optional):
#   MAP_PARTICIPANT_PERSONA         default: participant
#   MAP_PARTICIPANT_INTERVAL        default: 30
#   MAP_PARTICIPANT_STATE_FILE      default: .map/participant-bridge-state.json
#
# Claude credentials (resolved by the agent client itself):
#   ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN
#   ANTHROPIC_BASE_URL              optional, for Anthropic-compatible endpoints
#   ANTHROPIC_MODEL / CLAUDE_MODEL  optional model override

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PERSONA="${MAP_PARTICIPANT_PERSONA:-participant}"
INTERVAL="${MAP_PARTICIPANT_INTERVAL:-30}"
STATE_FILE="${MAP_PARTICIPANT_STATE_FILE:-.map/participant-bridge-state.json}"

if ! map --persona "$PERSONA" persona whoami >/dev/null 2>&1; then
  echo "error: map --persona $PERSONA persona whoami failed" >&2
  exit 1
fi

cmd=(python3 -m cli.participant_worker
  --persona "$PERSONA"
  --interval "$INTERVAL"
  --state-file "$STATE_FILE"
)

if [[ $# -gt 0 ]]; then
  cmd+=("$@")
fi

echo "Starting MAP participant bridge (claude-agent backend, persona=$PERSONA interval=${INTERVAL}s)" >&2
exec "${cmd[@]}"
