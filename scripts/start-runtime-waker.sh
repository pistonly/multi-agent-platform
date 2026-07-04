#!/usr/bin/env bash
# Default MAP waker entrypoint (simple-waker: poll todos → unified remind).
#
# Legacy per-item runtime-waker (SSE + fingerprints):
#   MAP_USE_LEGACY_WAKER=1 ./scripts/start-runtime-waker.sh
#   or: ./scripts/start-runtime-waker-claude.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ "${MAP_USE_LEGACY_WAKER:-0}" == "1" ]]; then
  exec "$ROOT/scripts/start-runtime-waker-claude.sh" "$@"
fi

exec "$ROOT/scripts/start-simple-waker.sh" "$@"
