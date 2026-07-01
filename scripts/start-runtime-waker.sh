#!/usr/bin/env bash
# Generic entrypoint for the MAP runtime waker.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/start-runtime-waker-claude.sh" "$@"
