#!/usr/bin/env bash
# Drive a scripted host ↔ participant ↔ reviewer collaboration flow end-to-end.
#
# Usage:
#   ./scripts/run-e2e-collab.sh
#   ./scripts/run-e2e-collab.sh --subject "..." --topic-title "..."
#   ./scripts/run-e2e-collab.sh --new-session
#   ./scripts/run-e2e-collab.sh --ignore-waker   # skip the waker-running guard
#
# This is a demo / E2E entry that spawns one Agent runtime per persona and
# sends one turn per step. It does NOT replace simple-waker. See
# cli/e2e_collab.py for the flow and AGENTS.md for the architecture boundary.
#
# Logs: .map/e2e-logs/<ts>/run.log (+ plan.md, execution-log.md, session logs).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

cmd=(python3 -m cli.main e2e run --project-root "$ROOT" "$@")
exec "${cmd[@]}"
