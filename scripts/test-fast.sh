#!/usr/bin/env bash
# Default fast gate: unit tests only (excludes slow / integration / claude_cli).
# Fails when wall-clock exceeds gate_max_seconds (see pyproject.toml).
# See tests/README.md and tests/PERF.md.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# T46：90s 是 2026-07 的预算（当时 112 例）。2026-09 实测用例涨到 2625，
# 本机 -n auto 墙钟 ~245s、CI 单 leg ~380s —— 旧预算恒红、必然被无视，
# 等于没有门禁。现按本机实测留 ~20% 余量设 300s；仍可用
# MAP_TEST_GATE_MAX_SECONDS 覆盖（CI 不跑本脚本，无影响）。
GATE_MAX_SECONDS="${MAP_TEST_GATE_MAX_SECONDS:-300}"
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
