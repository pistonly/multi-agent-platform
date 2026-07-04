#!/usr/bin/env bash
# Install or remove the map-wakers systemd unit.
#
# Usage:
#   ./scripts/systemd/map-wakers.service.install.sh
#   ./scripts/systemd/map-wakers.service.install.sh --dry-run
#   ./scripts/systemd/map-wakers.service.install.sh --uninstall
#   ./scripts/systemd/map-wakers.service.install.sh --project-root /path/to/repo
#
# Uses a user unit when not root (~/.config/systemd/user), otherwise system unit.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PROJECT_ROOT="$ROOT"
DRY_RUN=0
UNINSTALL=0

usage() {
  sed -n '2,9p' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-root)
      PROJECT_ROOT="${2:?}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --uninstall)
      UNINSTALL=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! command -v systemctl >/dev/null 2>&1; then
  echo "error: systemd not available (systemctl not found)" >&2
  exit 1
fi

UNIT_NAME="map-wakers.service"
TEMPLATE="$ROOT/scripts/systemd/map-wakers.service"
if [[ ! -f "$TEMPLATE" ]]; then
  echo "error: missing unit template $TEMPLATE" >&2
  exit 1
fi

if [[ "$(id -u)" -eq 0 ]]; then
  UNIT_DIR="/etc/systemd/system"
  SYSTEMCTL=(systemctl)
else
  UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  SYSTEMCTL=(systemctl --user)
fi

UNIT_PATH="$UNIT_DIR/$UNIT_NAME"
RENDERED="$(mktemp)"
trap 'rm -f "$RENDERED"' EXIT
sed "s|@PROJECT_ROOT@|$PROJECT_ROOT|g" "$TEMPLATE" >"$RENDERED"

run_systemctl() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] ${SYSTEMCTL[*]} $*"
    return 0
  fi
  if ! "${SYSTEMCTL[@]}" "$@"; then
    echo "error: systemctl $* failed (permissions or systemd unavailable)" >&2
    exit 1
  fi
}

if [[ "$UNINSTALL" -eq 1 ]]; then
  run_systemctl disable --now "$UNIT_NAME" || true
  if [[ "$DRY_RUN" -eq 0 && -f "$UNIT_PATH" ]]; then
    rm -f "$UNIT_PATH"
  else
    echo "[dry-run] rm -f $UNIT_PATH"
  fi
  run_systemctl daemon-reload
  echo "uninstalled $UNIT_NAME"
  exit 0
fi

if [[ "$DRY_RUN" -eq 0 ]]; then
  mkdir -p "$UNIT_DIR"
  if ! cp "$RENDERED" "$UNIT_PATH" 2>/dev/null; then
    echo "error: cannot write $UNIT_PATH (permission denied)" >&2
    exit 1
  fi
else
  echo "[dry-run] mkdir -p $UNIT_DIR"
  echo "[dry-run] cp $RENDERED $UNIT_PATH"
fi

run_systemctl daemon-reload
run_systemctl enable --now "$UNIT_NAME"
echo "installed $UNIT_NAME -> $UNIT_PATH"
