#!/usr/bin/env bash
# Bootstrap MAP for the current git repo. Requires MAP_ADMIN_TOKEN or ~/.map/admin.yaml
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"
exec map bootstrap "$@"
