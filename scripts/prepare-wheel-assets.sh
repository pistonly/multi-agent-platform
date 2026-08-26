#!/usr/bin/env bash
# Copy alembic.ini + alembic/ into server/_migrate so setuptools package-data
# can ship them in the wheel (T29). The source of truth stays at repo root;
# this directory is gitignored and regenerated at pack time.
# ``python -m build`` also does this via scripts/map_build_backend.py.
#
# Usage:
#   scripts/prepare-wheel-assets.sh
#   scripts/check-packaging.sh   # calls this, then python -m build
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/server/_migrate"

if [[ ! -f "$ROOT/alembic.ini" ]]; then
  echo "prepare-wheel-assets: missing $ROOT/alembic.ini" >&2
  exit 1
fi
if [[ ! -d "$ROOT/alembic/versions" ]]; then
  echo "prepare-wheel-assets: missing $ROOT/alembic/versions" >&2
  exit 1
fi

rm -rf "$DEST"
mkdir -p "$DEST"
cp "$ROOT/alembic.ini" "$DEST/alembic.ini"
cp -a "$ROOT/alembic" "$DEST/alembic"

if [[ ! -f "$DEST/alembic/env.py" ]]; then
  echo "prepare-wheel-assets: copy failed, $DEST/alembic/env.py missing" >&2
  exit 1
fi

echo "OK: $DEST (alembic.ini + alembic/)"
