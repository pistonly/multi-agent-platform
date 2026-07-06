#!/usr/bin/env bash
# Verify DEPRECATED manifest entries in docs/LEGACY-ENTRY-MATRIX.md still exist on disk.
# Exit 0 if all present; exit 1 if any missing or manifest unreadable.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="$ROOT/docs/LEGACY-ENTRY-MATRIX.md"

if [[ ! -f "$MANIFEST" ]]; then
  echo "check-deprecated: missing $MANIFEST" >&2
  exit 1
fi

missing=0
while IFS= read -r line; do
  path="${line#DEPRECATED: }"
  path="${path//$'\r'/}"
  if [[ -z "$path" ]]; then
    continue
  fi
  target="$ROOT/$path"
  if [[ ! -e "$target" ]]; then
    echo "check-deprecated: DEPRECATED entry missing on disk: $path" >&2
    missing=$((missing + 1))
  fi
done < <(grep -E '^DEPRECATED: ' "$MANIFEST" || true)

if [[ "$missing" -gt 0 ]]; then
  echo "check-deprecated: $missing missing entry/entries" >&2
  exit 1
fi

echo "check-deprecated: all manifest entries present"
