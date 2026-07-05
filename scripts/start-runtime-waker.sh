#!/usr/bin/env bash
# Default MAP waker entrypoint (simple-waker: poll todos → unified remind).
#
# Logs: .map/waker-logs/<persona>.log
# Stop with Ctrl+C.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

exec "$ROOT/scripts/start-simple-waker.sh" "$@"
