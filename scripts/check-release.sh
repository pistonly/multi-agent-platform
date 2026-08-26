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
#      == server-pkg/pyproject.toml [project] version
#      == server-pkg dependency multi-agent-platform[server]>=<version>
#      == uv.lock editable package version (if uv.lock exists)
#   2. with --require-tag: git tag v<version> exists and points at HEAD
#      AND server/web_dist/index.html exists (run scripts/sync-web-dist.sh
#      first so the wheel ships the board). Unpack check: scripts/check-packaging.sh.
#   3. with --require-tag: server/web_dist/build-info.json records that the
#      bundle was built from the current HEAD at the current version —
#      catches the incident class where a web/ source fix never reached a
#      rebuilt bundle (e.g. the TopicPage FS mark-read guard)
#
# MAP_RELEASE_ROOT overrides the repo root (tests / scripts/release.sh).
#
# Usage:
#   scripts/check-release.sh                # version consistency only
#   scripts/check-release.sh --require-tag  # + tag + bundled SPA (right before publish)

set -euo pipefail

ROOT="${MAP_RELEASE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
PYPROJECT="$ROOT/pyproject.toml"
SERVER_V="$ROOT/server/__version__.py"
SDK_V="$ROOT/sdk/python/map_sdk/__init__.py"
SERVER_PKG="$ROOT/server-pkg/pyproject.toml"
UV_LOCK="$ROOT/uv.lock"

for f in "$PYPROJECT" "$SERVER_V" "$SDK_V" "$SERVER_PKG"; do
  if [[ ! -f "$f" ]]; then
    echo "check-release: missing $f" >&2
    exit 1
  fi
done

read_project_version() {
  python3 -c '
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r"^\[project\].*?^version\s*=\s*\"([^\"]+)\"", text, re.M | re.S)
if m is None:
    sys.exit(1)
print(m.group(1))
' "$1"
}

ver_pyproject="$(read_project_version "$PYPROJECT")" || {
  echo "check-release: cannot parse [project] version from $PYPROJECT" >&2
  exit 1
}

ver_server="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$SERVER_V")"
ver_sdk="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$SDK_V")"
ver_server_pkg="$(read_project_version "$SERVER_PKG")" || {
  echo "check-release: cannot parse [project] version from $SERVER_PKG" >&2
  exit 1
}
ver_server_pkg_dep="$(python3 -c '
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r"multi-agent-platform\[server\]>=([^\"\s,]+)", text)
if m is None:
    sys.exit(1)
print(m.group(1))
' "$SERVER_PKG")" || {
  echo "check-release: cannot parse multi-agent-platform[server]>=... from $SERVER_PKG" >&2
  exit 1
}

if [[ -z "$ver_server" || -z "$ver_sdk" ]]; then
  echo "check-release: cannot parse __version__ from $SERVER_V / $SDK_V" >&2
  exit 1
fi

ver_uv=""
if [[ -f "$UV_LOCK" ]]; then
  ver_uv="$(python3 -c '
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
m = re.search(
    r"(?m)^name = \"multi-agent-platform\"\nversion = \"([^\"]+)\"\nsource = \{ editable = \"\.\" \}",
    text,
)
if m is None:
    sys.exit(1)
print(m.group(1))
' "$UV_LOCK")" || {
    echo "check-release: cannot parse editable multi-agent-platform version from $UV_LOCK" >&2
    exit 1
  }
fi

drift=0
if [[ "$ver_pyproject" != "$ver_server" || "$ver_pyproject" != "$ver_sdk" ]]; then
  drift=1
fi
if [[ "$ver_pyproject" != "$ver_server_pkg" || "$ver_pyproject" != "$ver_server_pkg_dep" ]]; then
  drift=1
fi
if [[ -n "$ver_uv" && "$ver_pyproject" != "$ver_uv" ]]; then
  drift=1
fi

if [[ "$drift" -ne 0 ]]; then
  cat >&2 <<EOF
check-release: version sources drifted — bump ALL of these in the same release commit:
  pyproject.toml [project] version                         = $ver_pyproject
  server/__version__.py __version__                        = $ver_server
  sdk/python/map_sdk/__init__.py                           = $ver_sdk
  server-pkg/pyproject.toml [project] version              = $ver_server_pkg
  server-pkg multi-agent-platform[server]>=...             = $ver_server_pkg_dep
EOF
  if [[ -f "$UV_LOCK" ]]; then
    echo "  uv.lock editable multi-agent-platform version            = ${ver_uv:-<unparsed>}" >&2
  fi
  echo "Guard test: tests/test_eng_version_single_source.py" >&2
  echo "Orchestrator: scripts/release.sh bump <version>" >&2
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

sources="pyproject / server / map_sdk / server-pkg"
if [[ -n "$ver_uv" ]]; then
  sources="$sources / uv.lock"
fi
echo "check-release: version $ver_pyproject consistent across $sources"
