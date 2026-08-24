#!/usr/bin/env bash
# Release pre-flight: version single-source consistency check.
#
# Background (M59 incident, see map/experiments/m59-perf-baseline-and-hygiene/):
# the PyPI 0.4.0 wheel shipped with a stale embedded version string
# (server.__version__ stayed 0.3.2) because the release commit forgot
# to bump one of the version copies. The pytest guard
# (tests/test_eng_version_single_source.py) catches drift on the next
# CI run — i.e. AFTER publishing. This script is the pre-publish gate.
#
# Checks (hard fail):
#   1. pyproject.toml [project] version
#      == server/__version__.py __version__
#      == sdk/python/map_sdk/__init__.py __version__
#   2. with --require-tag: git tag v<version> exists and points at HEAD
#      AND server/web_dist/index.html exists (run scripts/sync-web-dist.sh
#      first so the wheel ships the board)
#   3. with --require-tag: server/web_dist/build-info.json records that the
#      bundle was built from the current HEAD at the current version —
#      catches the incident class where a web/ source fix never reached a
#      rebuilt bundle (e.g. the TopicPage FS mark-read guard)
#
# Usage:
#   scripts/check-release.sh                # version consistency only
#   scripts/check-release.sh --require-tag  # + tag + bundled SPA (right before publish)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYPROJECT="$ROOT/pyproject.toml"
SERVER_V="$ROOT/server/__version__.py"
SDK_V="$ROOT/sdk/python/map_sdk/__init__.py"

for f in "$PYPROJECT" "$SERVER_V" "$SDK_V"; do
  if [[ ! -f "$f" ]]; then
    echo "check-release: missing $f" >&2
    exit 1
  fi
done

ver_pyproject="$(python3 -c '
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r"^\[project\].*?^version\s*=\s*\"([^\"]+)\"", text, re.M | re.S)
if m is None:
    sys.exit(1)
print(m.group(1))
' "$PYPROJECT")" || { echo "check-release: cannot parse [project] version from $PYPROJECT" >&2; exit 1; }

ver_server="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$SERVER_V")"
ver_sdk="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$SDK_V")"
if [[ -z "$ver_server" || -z "$ver_sdk" ]]; then
  echo "check-release: cannot parse __version__ from $SERVER_V / $SDK_V" >&2
  exit 1
fi

if [[ "$ver_pyproject" != "$ver_server" || "$ver_pyproject" != "$ver_sdk" ]]; then
  cat >&2 <<EOF
check-release: version sources drifted — bump ALL THREE in the same release commit:
  pyproject.toml [project] version       = $ver_pyproject
  server/__version__.py __version__      = $ver_server
  sdk/python/map_sdk/__init__.py         = $ver_sdk
Guard test: tests/test_eng_version_single_source.py
EOF
  exit 1
fi

if [[ "${1:-}" == "--require-tag" ]]; then
  web_index="$ROOT/server/web_dist/index.html"
  if [[ ! -f "$web_index" ]]; then
    echo "check-release: missing $web_index — run scripts/sync-web-dist.sh before publish so map-server ships the board" >&2
    exit 1
  fi
  build_info="$ROOT/server/web_dist/build-info.json"
  if [[ ! -f "$build_info" ]]; then
    echo "check-release: missing $build_info — run scripts/sync-web-dist.sh before publish so the board provably matches this source" >&2
    exit 1
  fi
  built_sha="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["git_sha"])' "$build_info")"
  built_ver="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$build_info")"
  head_sha="$(git -C "$ROOT" rev-parse HEAD)"
  if [[ "$built_sha" != "$head_sha" || "$built_ver" != "$ver_pyproject" ]]; then
    cat >&2 <<EOF
check-release: server/web_dist was built from ${built_sha:-<none>} (version ${built_ver:-<none>}),
but this release is HEAD=$head_sha version=$ver_pyproject. The shipped board may predate a
web/ source change whose fix never reached the bundle.
Run: scripts/sync-web-dist.sh   # rebuilds web/dist -> server/web_dist + build-info.json
EOF
    exit 1
  fi
  tag="v$ver_pyproject"
  if ! git -C "$ROOT" rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
    echo "check-release: git tag $tag not found (create: git tag $tag && git push origin $tag)" >&2
    exit 1
  fi
  if [[ "$(git -C "$ROOT" rev-parse HEAD)" != "$(git -C "$ROOT" rev-parse "$tag^{commit}")" ]]; then
    echo "check-release: git tag $tag exists but does not point at HEAD" >&2
    exit 1
  fi
fi

echo "check-release: version $ver_pyproject consistent across pyproject / server / map_sdk"
