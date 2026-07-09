#!/usr/bin/env bash
# Stop all MAP wakers together (simple-waker).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

exec "$ROOT/scripts/stop-all-simple-wakers.sh" "$@"
