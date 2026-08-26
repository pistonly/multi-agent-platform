#!/usr/bin/env bash
# T29 packaging smoke: sdist contains alembic.ini + alembic/versions;
# wheel contains server/web_dist (SPA) + server/_migrate (alembic).
#
# Usage:
#   scripts/sync-web-dist.sh && scripts/check-packaging.sh
#   scripts/check-packaging.sh --skip-web   # alembic-only; still needs a
#                                           # placeholder index.html if none
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
SKIP_WEB="${SKIP_WEB:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-web) SKIP_WEB=1; shift ;;
    --help|-h)
      sed -n '2,12p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

bash "$ROOT/scripts/prepare-wheel-assets.sh"

web_index="$ROOT/server/web_dist/index.html"
if [[ ! -f "$web_index" ]]; then
  if [[ -n "$SKIP_WEB" ]]; then
    mkdir -p "$ROOT/server/web_dist/assets"
    printf '<!doctype html><title>packaging-placeholder</title>\n' >"$web_index"
    printf 'placeholder\n' >"$ROOT/server/web_dist/assets/.keep"
    echo "check-packaging: wrote placeholder $web_index (--skip-web)" >&2
  else
    echo "check-packaging: missing $web_index — run scripts/sync-web-dist.sh" >&2
    exit 1
  fi
fi

PYTHON="${PYTHON:-python3}"
rm -rf "$ROOT/dist"
"$PYTHON" -m build
shopt -s nullglob
wheels=(dist/*.whl)
sdists=(dist/*.tar.gz)
if [[ ${#wheels[@]} -ne 1 || ${#sdists[@]} -ne 1 ]]; then
  echo "check-packaging: expected one wheel and one sdist in dist/, got wheels=${#wheels[@]} sdists=${#sdists[@]}" >&2
  ls -la dist || true
  exit 1
fi

"$PYTHON" - "${wheels[0]}" "${sdists[0]}" <<'PY'
import sys
import tarfile
import zipfile
from pathlib import Path

wheel = Path(sys.argv[1])
sdist = Path(sys.argv[2])


def norm(name: str) -> str:
    return name.replace("\\", "/")


with zipfile.ZipFile(wheel) as zf:
    names = [norm(n) for n in zf.namelist()]
wheel_ok = any(n.endswith("server/web_dist/index.html") for n in names)
assets = [
    n
    for n in names
    if "server/web_dist/assets/" in n
]
migrate_ini = any(n.endswith("server/_migrate/alembic.ini") for n in names)
migrate_env = any(n.endswith("server/_migrate/alembic/env.py") for n in names)
versions = [
    n
    for n in names
    if "server/_migrate/alembic/versions/" in n and n.endswith(".py")
]
if not wheel_ok:
    raise SystemExit(f"wheel missing server/web_dist/index.html: {wheel}")
if not assets:
    raise SystemExit(f"wheel server/web_dist/assets is empty: {wheel}")
if not migrate_ini or not migrate_env or not versions:
    raise SystemExit(
        f"wheel missing alembic bundle (ini={migrate_ini} env={migrate_env} versions={len(versions)}): {wheel}"
    )

with tarfile.open(sdist, "r:gz") as tf:
    members = [norm(m.name) for m in tf.getmembers()]
has_ini = any(n.endswith("alembic.ini") and "server/_migrate/" not in n for n in members)
has_versions = any("/alembic/versions/" in n and n.endswith(".py") for n in members)
if not has_ini:
    raise SystemExit(f"sdist missing alembic.ini: {sdist}")
if not has_versions:
    raise SystemExit(f"sdist missing alembic/versions/*.py: {sdist}")

print(f"OK: {wheel.name} web_dist + {len(versions)} migrations; {sdist.name} alembic.ini + versions")
PY
