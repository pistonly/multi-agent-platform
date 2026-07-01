#!/usr/bin/env bash
# Start a MAP runtime waker backed by resumable Agent Runtime sessions.
#
# Usage:
#   ./scripts/start-runtime-waker-claude.sh --persona host
#   MAP_RUNTIME_PERSONA=reviewer ./scripts/start-runtime-waker-claude.sh
#   MAP_RUNTIME_BACKEND=codex ./scripts/start-runtime-waker-claude.sh --persona host
#   MAP_RUNTIME_BACKEND=cursor ./scripts/start-runtime-waker-claude.sh --persona host
#   ./scripts/start-runtime-waker-claude.sh --once --dry-run

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PERSONA="${MAP_RUNTIME_PERSONA:-host}"
BACKEND="${MAP_RUNTIME_BACKEND:-claude}"
INTERVAL="${MAP_RUNTIME_INTERVAL:-30}"
STATE_FILE="${MAP_RUNTIME_STATE_FILE:-.map/runtime-waker-state.json}"
# Let `./script.sh --persona reviewer` override MAP_RUNTIME_PERSONA default.
_cli_args=("$@")
for ((i = 0; i < ${#_cli_args[@]}; i++)); do
  if [[ "${_cli_args[$i]}" == "--persona" && $((i + 1)) -lt ${#_cli_args[@]} ]]; then
    PERSONA="${_cli_args[$((i + 1))]}"
    break
  fi
done
unset _cli_args
if [[ -n "${MAP_RUNTIME_HOME:-}" ]]; then
  RUNTIME_HOME="$MAP_RUNTIME_HOME"
elif [[ "$BACKEND" == "codex" ]]; then
  RUNTIME_HOME=".map/codex-runtime-home"
else
  RUNTIME_HOME=".map/claude-runtime-home"
fi
MAX_WAKES="${MAP_RUNTIME_MAX_WAKES_PER_CYCLE:-3}"
COOLDOWN="${MAP_RUNTIME_COOLDOWN_SECONDS:-300}"

if ! map --persona "$PERSONA" persona whoami >/dev/null 2>&1; then
  echo "error: map --persona $PERSONA persona whoami failed (check .map/ and MAP API)" >&2
  exit 1
fi

mkdir -p "$RUNTIME_HOME/.claude"

cmd=(python3 -m cli.runtime_waker
  --persona "$PERSONA"
  --backend "$BACKEND"
  --project-root "$ROOT"
  --interval "$INTERVAL"
  --state-file "$STATE_FILE"
  --runtime-home "$RUNTIME_HOME"
  --max-wakes-per-cycle "$MAX_WAKES"
  --cooldown-seconds "$COOLDOWN"
)

if [[ -n "${MAP_RUNTIME_MODEL:-}" ]]; then
  cmd+=(--model "$MAP_RUNTIME_MODEL")
fi

if [[ -n "${MAP_RUNTIME_CODEX_BIN:-}" ]]; then
  cmd+=(--codex-bin "$MAP_RUNTIME_CODEX_BIN")
fi

if [[ "${MAP_RUNTIME_FORCE:-0}" == "1" ]]; then
  cmd+=(--force)
fi

if [[ "${MAP_RUNTIME_PARTICIPANT_OPEN_TOPICS:-1}" == "0" ]]; then
  cmd+=(--no-participant-open-topics)
fi

if [[ $# -gt 0 ]]; then
  cmd+=("$@")
fi

echo "Starting MAP runtime waker (backend=$BACKEND persona=$PERSONA interval=${INTERVAL}s state=$STATE_FILE runtime_home=$RUNTIME_HOME)" >&2
echo "Stop with Ctrl+C" >&2
exec "${cmd[@]}"
