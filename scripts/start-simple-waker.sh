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

# LLM 凭据/端点/模型不再由脚本处理：cli.simple_waker 的 run() 会调用
# cli.agent_client.apply_project_claude_env,以 .map/.claude-env 为权威覆盖并
# 清理继承的 ANTHROPIC_*(任何启动路径、包括 nohup 直启,都不会落回 z.ai 等
# 残留端点)。脚本只负责默认值与编排,CLI 是唯一真源。

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
