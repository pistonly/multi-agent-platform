#!/usr/bin/env bash
# Local release conductor. Composes existing gates; does not invent a second
# version source of truth. Does not push remotes. Does not upload to PyPI
# unless `upload --yes` is passed.
#
# Usage:
#   scripts/release.sh bump 0.10.0 [--dry-run]   # edit version files; do not commit
#   then review + commit the bump
#   scripts/release.sh prepare [--dry-run]       # SPA + unpack-check + dual-package build
#   scripts/release.sh tag [--dry-run]           # annotated v<version> at HEAD; no push
#   scripts/release.sh upload [--yes] [--repository NAME] [--skip-existing]
#   scripts/release.sh status
#
# MAP_RELEASE_ROOT overrides the repo root (tests).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export MAP_RELEASE_ROOT="${MAP_RELEASE_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
ROOT="$MAP_RELEASE_ROOT"
cd "$ROOT"

usage() {
  awk 'NR==1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "$0"
}

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

current_version() {
  read_project_version "$ROOT/pyproject.toml"
}

is_git_repo() {
  git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1
}

require_clean_tree() {
  if ! is_git_repo; then
    return 0
  fi
  if ! git -C "$ROOT" diff --quiet || ! git -C "$ROOT" diff --cached --quiet; then
    echo "release: working tree has staged or unstaged changes; commit or stash first" >&2
    exit 1
  fi
}

warn_remote_divergence() {
  if ! is_git_repo; then
    return 0
  fi
  local head remote_sha
  head="$(git -C "$ROOT" rev-parse HEAD)"
  local remote
  for remote in origin github; do
    if ! git -C "$ROOT" remote get-url "$remote" >/dev/null 2>&1; then
      continue
    fi
    if command -v timeout >/dev/null 2>&1; then
      remote_sha="$(timeout 5 git -C "$ROOT" ls-remote "$remote" refs/heads/main 2>/dev/null | awk '{print $1}' || true)"
    else
      remote_sha="$(git -C "$ROOT" ls-remote "$remote" refs/heads/main 2>/dev/null | awk '{print $1}' || true)"
    fi
    if [[ -n "$remote_sha" && "$remote_sha" != "$head" ]]; then
      echo "release: WARN $remote/main is $remote_sha, HEAD is $head — align remotes before pushing the tag" >&2
    fi
  done
}

print_push_reminder() {
  local tag="$1"
  cat <<EOF
release: not pushing remotes (origin is the intranet source; github is the public/PyPI baseline).
After you confirm:
  git push origin HEAD && git push origin $tag
  git push github HEAD && git push github $tag
EOF
}

cmd_status() {
  bash "$SCRIPT_DIR/check-release.sh"
  local ver tag
  ver="$(current_version)"
  tag="v$ver"
  if is_git_repo; then
    if git -C "$ROOT" rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
      local tag_sha head
      tag_sha="$(git -C "$ROOT" rev-parse "$tag^{commit}")"
      head="$(git -C "$ROOT" rev-parse HEAD)"
      if [[ "$tag_sha" == "$head" ]]; then
        echo "release: tag $tag points at HEAD"
      else
        echo "release: tag $tag exists but does not point at HEAD ($tag_sha vs $head)" >&2
      fi
    else
      echo "release: tag $tag not created yet"
    fi
  fi
  if [[ -f "$ROOT/server/web_dist/build-info.json" ]]; then
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("release: web_dist git_sha=%s version=%s" % (d.get("git_sha"), d.get("version")))' \
      "$ROOT/server/web_dist/build-info.json"
  else
    echo "release: server/web_dist/build-info.json missing (run prepare)"
  fi
}

validate_version() {
  python3 -c '
import re, sys
ver = sys.argv[1]
if re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?", ver) is None:
    sys.exit(1)
' "$1"
}

cmd_bump() {
  local new="" dry=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) dry=1; shift ;;
      --help|-h) usage; exit 0 ;;
      -*)
        echo "release bump: unknown option $1" >&2
        exit 1
        ;;
      *)
        if [[ -n "$new" ]]; then
          echo "release bump: unexpected extra argument $1" >&2
          exit 1
        fi
        new="$1"
        shift
        ;;
    esac
  done
  if [[ -z "$new" ]]; then
    echo "release bump: missing version (example: scripts/release.sh bump 0.10.0)" >&2
    exit 1
  fi
  if ! validate_version "$new"; then
    echo "release bump: $new is not MAJOR.MINOR.PATCH (optional -pre / +build)" >&2
    exit 1
  fi
  local old
  old="$(current_version)"
  if [[ "$old" == "$new" ]]; then
    echo "release bump: already at $new" >&2
    exit 1
  fi
  echo "release: $old -> $new"
  echo "  pyproject.toml"
  echo "  server/__version__.py"
  echo "  sdk/python/map_sdk/__init__.py"
  echo "  server-pkg/pyproject.toml (version + dependency pin)"
  if [[ -f "$ROOT/uv.lock" ]]; then
    echo "  uv.lock (uv lock)"
  fi
  if [[ "$dry" -eq 1 ]]; then
    echo "release: dry-run, no files written"
    return 0
  fi
  require_clean_tree
  python3 - "$ROOT" "$new" <<'PY'
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
new = sys.argv[2]


def replace_project_version(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r'(?ms)^(\[project\].*?^version\s*=\s*")[^"]+(")',
        rf"\g<1>{new}\2",
        text,
        count=1,
    )
    if n != 1:
        raise SystemExit(f"release bump: cannot replace [project] version in {path}")
    path.write_text(new_text, encoding="utf-8")


def replace_dunder(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    new_text, n = re.subn(
        r'(?m)^(__version__ = ")[^"]+(")',
        rf"\g<1>{new}\2",
        text,
        count=1,
    )
    if n != 1:
        raise SystemExit(f"release bump: cannot replace __version__ in {path}")
    path.write_text(new_text, encoding="utf-8")


replace_project_version(root / "pyproject.toml")
replace_dunder(root / "server" / "__version__.py")
replace_dunder(root / "sdk" / "python" / "map_sdk" / "__init__.py")

pkg = root / "server-pkg" / "pyproject.toml"
replace_project_version(pkg)
text = pkg.read_text(encoding="utf-8")
new_text, n = re.subn(
    r"(multi-agent-platform\[server\]>=)[^\"\s,]+",
    rf"\g<1>{new}",
    text,
    count=1,
)
if n != 1:
    raise SystemExit(f"release bump: cannot replace server extra pin in {pkg}")
pkg.write_text(new_text, encoding="utf-8")
PY
  if [[ -f "$ROOT/uv.lock" ]]; then
    if ! command -v uv >/dev/null 2>&1; then
      echo "release bump: uv.lock exists but uv is not on PATH" >&2
      exit 1
    fi
    (cd "$ROOT" && uv lock)
  fi
  bash "$SCRIPT_DIR/check-release.sh"
  cat <<EOF
release: files updated to $new. Review, then:
  git add pyproject.toml server/__version__.py sdk/python/map_sdk/__init__.py server-pkg/pyproject.toml uv.lock
  git commit -m "release: v$new"
  scripts/release.sh prepare
EOF
}

cmd_prepare() {
  local dry=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) dry=1; shift ;;
      --help|-h) usage; exit 0 ;;
      *)
        echo "release prepare: unknown argument $1" >&2
        exit 1
        ;;
    esac
  done
  echo "release prepare:"
  echo "  1. scripts/check-release.sh"
  echo "  2. scripts/sync-bundled-skills.sh (if .cursor/skills and cli/skills drifted)"
  echo "  3. scripts/sync-web-dist.sh"
  echo "  4. scripts/check-packaging.sh"
  echo "  5. python -m build server-pkg"
  if [[ "$dry" -eq 1 ]]; then
    echo "release: dry-run, no build"
    return 0
  fi
  require_clean_tree
  bash "$SCRIPT_DIR/check-release.sh"
  if [[ -d "$ROOT/.cursor/skills" && -d "$ROOT/cli/skills" ]]; then
    if ! diff -r -x '__init__.py' "$ROOT/.cursor/skills" "$ROOT/cli/skills" >/dev/null; then
      echo "release prepare: .cursor/skills and cli/skills drifted — run scripts/sync-bundled-skills.sh" >&2
      exit 1
    fi
  fi
  bash "$SCRIPT_DIR/sync-web-dist.sh"
  bash "$SCRIPT_DIR/check-packaging.sh"
  rm -rf "$ROOT/server-pkg/dist"
  # 解释器可覆盖：本机常见多 Python 共存（homebrew python3 无 build 模块、
  # 项目环境在别处），`MAP_RELEASE_PYTHON=python ./scripts/release.sh prepare`。
  (cd "$ROOT/server-pkg" && "${MAP_RELEASE_PYTHON:-python3}" -m build)
  echo "release: prepare ok. Next: scripts/release.sh tag"
}

cmd_tag() {
  local dry=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) dry=1; shift ;;
      --help|-h) usage; exit 0 ;;
      *)
        echo "release tag: unknown argument $1" >&2
        exit 1
        ;;
    esac
  done
  if ! is_git_repo; then
    echo "release tag: $ROOT is not a git repository" >&2
    exit 1
  fi
  local ver tag
  ver="$(current_version)"
  tag="v$ver"
  echo "release tag: $tag at HEAD $(git -C "$ROOT" rev-parse --short HEAD)"
  if [[ "$dry" -eq 1 ]]; then
    echo "release: dry-run, tag not created, remotes not pushed"
    return 0
  fi
  require_clean_tree
  bash "$SCRIPT_DIR/check-release.sh"
  if git -C "$ROOT" rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
    echo "release tag: $tag already exists" >&2
    exit 1
  fi
  bash "$SCRIPT_DIR/sync-web-dist.sh"
  git -C "$ROOT" tag -a "$tag" -m "release: $tag"
  bash "$SCRIPT_DIR/check-release.sh" --require-tag
  warn_remote_divergence
  print_push_reminder "$tag"
  echo "release: next scripts/release.sh upload --yes"
}

cmd_upload() {
  local yes=0 repository="pypi" skip_existing=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --yes) yes=1; shift ;;
      --repository)
        repository="${2:-}"
        if [[ -z "$repository" ]]; then
          echo "release upload: --repository needs a name (pypi / testpypi)" >&2
          exit 1
        fi
        shift 2
        ;;
      --skip-existing) skip_existing=1; shift ;;
      --help|-h) usage; exit 0 ;;
      *)
        echo "release upload: unknown argument $1" >&2
        exit 1
        ;;
    esac
  done
  local ver
  ver="$(current_version)"
  local twine_args=(upload --repository "$repository")
  if [[ "$skip_existing" -eq 1 ]]; then
    twine_args+=(--skip-existing)
  fi
  echo "release upload: $ver to $repository (main package first, then server-pkg meta)"
  echo "  twine ${twine_args[*]} dist/multi_agent_platform-${ver}*"
  echo "  twine ${twine_args[*]} server-pkg/dist/multi_agent_platform_server-${ver}*"
  echo "release: will not git push origin or github"
  if [[ "$yes" -ne 1 ]]; then
    echo "release: dry-run (pass --yes to upload)"
    return 0
  fi
  require_clean_tree
  if ! command -v twine >/dev/null 2>&1; then
    echo "release upload: twine is not on PATH" >&2
    exit 1
  fi
  bash "$SCRIPT_DIR/check-release.sh" --require-tag
  shopt -s nullglob
  local main_files=(
    "$ROOT/dist/multi_agent_platform-${ver}-"*
    "$ROOT/dist/multi_agent_platform-${ver}.tar.gz"
  )
  local meta_files=(
    "$ROOT/server-pkg/dist/multi_agent_platform_server-${ver}-"*
    "$ROOT/server-pkg/dist/multi_agent_platform_server-${ver}.tar.gz"
  )
  shopt -u nullglob
  if [[ ${#main_files[@]} -eq 0 ]]; then
    echo "release upload: missing dist/ artifacts for $ver — run scripts/release.sh prepare" >&2
    exit 1
  fi
  if [[ ${#meta_files[@]} -eq 0 ]]; then
    echo "release upload: missing server-pkg/dist artifacts for $ver — run scripts/release.sh prepare" >&2
    exit 1
  fi
  twine "${twine_args[@]}" "${main_files[@]}"
  twine "${twine_args[@]}" "${meta_files[@]}"
  warn_remote_divergence
  print_push_reminder "v$ver"
  echo "release: uploaded $ver. Verify: pip index versions multi-agent-platform"
}

cmd="${1:-}"
if [[ $# -gt 0 ]]; then
  shift
fi

case "$cmd" in
  bump) cmd_bump "$@" ;;
  prepare) cmd_prepare "$@" ;;
  tag) cmd_tag "$@" ;;
  upload) cmd_upload "$@" ;;
  status) cmd_status "$@" ;;
  -h|--help|help|"")
    usage
    if [[ -z "$cmd" ]]; then
      exit 1
    fi
    ;;
  *)
    echo "release: unknown command $cmd" >&2
    usage >&2
    exit 1
    ;;
esac
