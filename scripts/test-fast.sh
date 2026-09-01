#!/usr/bin/env bash
# Default fast gate: unit tests only (excludes slow / integration / claude_cli).
# Fails when wall-clock exceeds gate_max_seconds (see pyproject.toml).
# See tests/README.md and tests/PERF.md.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GATE_MAX_SECONDS="${MAP_TEST_GATE_MAX_SECONDS:-90}"
START=$(date +%s)

python3 -m pytest -m "not slow and not integration and not claude_cli" -n auto "$@"
STATUS=$?

END=$(date +%s)
DURATION=$((END - START))
echo "test-fast: ${DURATION}s elapsed (gate_max_seconds=${GATE_MAX_SECONDS})"

if [[ "$DURATION" -gt "$GATE_MAX_SECONDS" ]]; then
  echo "FAST GATE FAIL: ${DURATION}s > ${GATE_MAX_SECONDS}s" >&2
  exit 1
fi

exit "$STATUS"
