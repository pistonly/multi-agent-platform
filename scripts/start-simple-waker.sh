#!/usr/bin/env bash
# Start the simplified MAP waker (poll todos → unified remind prompt).
#
# Usage:
#   ./scripts/start-simple-waker.sh --persona host
#   MAP_SIMPLE_PERSONA=reviewer ./scripts/start-simple-waker.sh
#   ./scripts/start-simple-waker.sh --once --dry-run

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 固定读取项目级 LLM 凭证 .map/.claude-env,覆盖启动 shell 可能残留的 ANTHROPIC_*
# (否则 waker 会落到错误端点/账号,如 z.ai 的 5 小时 429 用量上限)。set -a 确保
# source 出的变量全部 export 给后续 exec 的 python 进程。
map_env="$ROOT/.map/.claude-env"
if [[ -f "$map_env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$map_env"
  set +a
fi

PERSONA="${MAP_SIMPLE_PERSONA:-${MAP_RUNTIME_PERSONA:-host}}"
ACTIVE_INTERVAL="${MAP_SIMPLE_ACTIVE_INTERVAL:-${MAP_RUNTIME_INTERVAL:-30}}"
IDLE_INTERVAL="${MAP_SIMPLE_IDLE_INTERVAL:-300}"
MIN_REMIND="${MAP_SIMPLE_MIN_REMIND_SECONDS:-30}"
STATE_FILE="${MAP_SIMPLE_STATE_FILE:-.map/simple-waker-state.json}"
RUNTIME_HOME="${MAP_SIMPLE_RUNTIME_HOME:-${MAP_RUNTIME_HOME:-.map/claude-runtime-home}}"

_cli_args=("$@")
for ((i = 0; i < ${#_cli_args[@]}; i++)); do
  if [[ "${_cli_args[$i]}" == "--persona" && $((i + 1)) -lt ${#_cli_args[@]} ]]; then
    PERSONA="${_cli_args[$((i + 1))]}"
    break
  fi
done
unset _cli_args

if ! map --persona "$PERSONA" persona whoami >/dev/null 2>&1; then
  echo "error: map --persona $PERSONA persona whoami failed (check .map/ and MAP API)" >&2
  exit 1
fi

mkdir -p "$RUNTIME_HOME/.claude"

cmd=(python3 -m cli.simple_waker
  --persona "$PERSONA"
  --project-root "$ROOT"
  --active-interval "$ACTIVE_INTERVAL"
  --idle-interval "$IDLE_INTERVAL"
  --min-remind-seconds "$MIN_REMIND"
  --state-file "$STATE_FILE"
  --runtime-home "$RUNTIME_HOME"
)

if [[ -n "${MAP_SIMPLE_MODEL:-${MAP_RUNTIME_MODEL:-}}" ]]; then
  cmd+=(--model "${MAP_SIMPLE_MODEL:-${MAP_RUNTIME_MODEL}}")
fi

if [[ $# -gt 0 ]]; then
  cmd+=("$@")
fi

echo "Starting MAP simple waker (persona=$PERSONA active=${ACTIVE_INTERVAL}s idle=${IDLE_INTERVAL}s state=$STATE_FILE)" >&2
echo "Stop with Ctrl+C" >&2
exec "${cmd[@]}"
