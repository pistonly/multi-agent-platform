#!/usr/bin/env bash
# Copy the Vite production build into server/web_dist so map-server / the
# PyPI wheel can serve the board from the same origin as the API.
#
# Usage:
#   scripts/sync-web-dist.sh              # npm run build, then copy
#   scripts/sync-web-dist.sh --skip-build # copy an existing web/dist
#
# Release: run this before `python -m build` / `uv build` so the sdist and
# wheel contain server/web_dist/index.html. See scripts/check-release.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEB="$ROOT/web"
DEST="$ROOT/server/web_dist"
SKIP_BUILD="${SKIP_BUILD:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1; shift ;;
    --help|-h)
      sed -n '2,16p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$SKIP_BUILD" ]]; then
  if [[ ! -d "$WEB/node_modules" ]]; then
    echo "sync-web-dist: installing web dependencies" >&2
    (cd "$WEB" && npm ci)
  fi
  echo "sync-web-dist: building web/" >&2
  (cd "$WEB" && npm run build)
fi

if [[ ! -f "$WEB/dist/index.html" ]]; then
  echo "sync-web-dist: missing $WEB/dist/index.html (run without --skip-build)" >&2
  exit 1
fi

mkdir -p "$DEST"
# --delete keeps hashed asset filenames from accumulating across builds.
# Keep .gitkeep (tracked placeholder); it is not part of web/dist.
rsync -a --delete --exclude='.gitkeep' "$WEB/dist/" "$DEST/"

if [[ ! -f "$DEST/index.html" ]]; then
  echo "sync-web-dist: copy failed, $DEST/index.html missing" >&2
  exit 1
fi

# Record the exact source this bundle was built from. check-release.sh
# --require-tag fails a publish whose server/web_dist predates the latest
# web/ source (the incident class where a fix landed in source but the
# shipped board kept the old behaviour).
build_info="$DEST/build-info.json"
git_sha="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
version="$(python3 -c '
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r"^\[project\].*?^version\s*=\s*\"([^\"]+)\"", text, re.M | re.S)
print(m.group(1) if m else "unknown")
' "$ROOT/pyproject.toml")"
cat > "$build_info" <<EOF
{
  "git_sha": "$git_sha",
  "version": "$version",
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
echo "check-release: wrote $build_info (git $git_sha, version $version)"

echo "OK: $DEST (from web/dist)"
