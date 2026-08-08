#!/usr/bin/env bash
# Bootstrap MAP for the current git repo. New server (>=0.4) needs no admin token;
# falls back to MAP_ADMIN_TOKEN / ~/.map/admin.yaml on older servers.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"
exec map bootstrap "$@"
