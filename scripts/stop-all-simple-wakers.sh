#!/usr/bin/env bash
# Stop MAP simple wakers started for this repository.
#
# Usage:
#   ./scripts/stop-all-simple-wakers.sh
#   ./scripts/stop-all-simple-wakers.sh --dry-run
#   ./scripts/stop-all-simple-wakers.sh --force

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TIMEOUT_SECONDS="${MAP_WAKER_STOP_TIMEOUT_SECONDS:-15}"
FORCE=0
DRY_RUN=0

usage() {
  cat >&2 <<'EOF'
Usage:
  ./scripts/stop-all-simple-wakers.sh [--dry-run] [--force] [--timeout-seconds N]

Options:
  --dry-run
      Print matching waker processes without stopping them.
  --force
      Send SIGKILL to any matching processes still alive after SIGTERM timeout.
  --timeout-seconds N
      Seconds to wait after SIGTERM before reporting leftovers. Default: 15.

Environment:
  MAP_WAKER_STOP_TIMEOUT_SECONDS  Default timeout when --timeout-seconds is omitted.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --timeout-seconds)
      if [[ $# -lt 2 ]]; then
        echo "error: --timeout-seconds requires a value" >&2
        exit 2
      fi
      TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if ! [[ "$TIMEOUT_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "error: --timeout-seconds must be a non-negative integer" >&2
  exit 2
fi

find_wakers() {
  python3 - "$ROOT" <<'PY'
import os
import subprocess
import sys

root = os.path.realpath(sys.argv[1])
script_all = os.path.join(root, "scripts", "start-all-simple-wakers.sh")
script_alias = os.path.join(root, "scripts", "start-all-wakers.sh")
script_one = os.path.join(root, "scripts", "start-simple-waker.sh")
exclude = {os.getpid(), os.getppid()}

rows: list[tuple[int, int, str]] = []
result = subprocess.run(
    ["ps", "-eo", "pid=,ppid=,args="],
    text=True,
    stdout=subprocess.PIPE,
    check=True,
)
for line in result.stdout.splitlines():
    parts = line.strip().split(None, 2)
    if len(parts) < 3:
        continue
    try:
        pid = int(parts[0])
        ppid = int(parts[1])
    except ValueError:
        continue
    if pid in exclude:
        continue
    cmd = parts[2]
    real_cmd = cmd.replace(script_all, "<start-all-simple-wakers>")
    real_cmd = real_cmd.replace(script_alias, "<start-all-wakers>")
    real_cmd = real_cmd.replace(script_one, "<start-simple-waker>")

    is_parent = (
        script_all in cmd
        or script_alias in cmd
        or (
            "start-all-simple-wakers.sh" in cmd
            and root in cmd
        )
    )
    is_child = (
        " -m cli.simple_waker" in f" {cmd}"
        and (
            f"--project-root {root}" in cmd
            or f"--project-root={root}" in cmd
            or f"--project-root '{root}'" in cmd
            or f'--project-root "{root}"' in cmd
        )
    )
    if is_parent or is_child:
        rows.append((pid, ppid, real_cmd))

for pid, ppid, cmd in sorted(rows):
    print(f"{pid}\t{ppid}\t{cmd}")
PY
}

mapfile -t rows < <(find_wakers)

if [[ "${#rows[@]}" -eq 0 ]]; then
  echo "No simple waker processes found for $ROOT"
  exit 0
fi

echo "Matched simple waker processes for $ROOT:"
printf '  %s\n' "${rows[@]}"

if [[ "$DRY_RUN" == "1" ]]; then
  exit 0
fi

pids=()
parent_pids=()
for row in "${rows[@]}"; do
  IFS=$'\t' read -r pid _ppid cmd <<<"$row"
  pids+=("$pid")
  if [[ "$cmd" == *"<start-all-simple-wakers>"* || "$cmd" == *"<start-all-wakers>"* ]]; then
    parent_pids+=("$pid")
  fi
done

if [[ "${#parent_pids[@]}" -gt 0 ]]; then
  echo "Sending SIGTERM to waker parent process(es): ${parent_pids[*]}"
  kill -TERM "${parent_pids[@]}" 2>/dev/null || true
else
  echo "No parent launcher found; sending SIGTERM to matching simple-waker process(es): ${pids[*]}"
  kill -TERM "${pids[@]}" 2>/dev/null || true
fi

deadline=$((SECONDS + TIMEOUT_SECONDS))
while (( SECONDS < deadline )); do
  alive=()
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      alive+=("$pid")
    fi
  done
  if [[ "${#alive[@]}" -eq 0 ]]; then
    echo "All simple waker processes stopped."
    exit 0
  fi
  sleep 1
done

alive=()
for pid in "${pids[@]}"; do
  if kill -0 "$pid" 2>/dev/null; then
    alive+=("$pid")
  fi
done

if [[ "${#alive[@]}" -eq 0 ]]; then
  echo "All simple waker processes stopped."
  exit 0
fi

if [[ "$FORCE" == "1" ]]; then
  echo "Processes still alive after ${TIMEOUT_SECONDS}s; sending SIGKILL: ${alive[*]}" >&2
  kill -KILL "${alive[@]}" 2>/dev/null || true
  echo "Forced stop requested; verify with: ps -eo pid,ppid,stat,cmd | rg 'cli\\.simple_waker|start-all-simple-wakers|start-simple-waker'"
  exit 0
fi

echo "warning: processes still alive after ${TIMEOUT_SECONDS}s: ${alive[*]}" >&2
echo "Run with --force to SIGKILL leftovers, or inspect manually:" >&2
echo "  ps -eo pid,ppid,stat,cmd | rg 'cli\\.simple_waker|start-all-simple-wakers|start-simple-waker'" >&2
exit 1
