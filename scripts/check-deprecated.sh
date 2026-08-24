#!/usr/bin/env bash
# Verify DEPRECATED manifest entries in docs/LEGACY-ENTRY-MATRIX.md still exist on disk.
# Exit 0 if all present; exit 1 if any missing or manifest unreadable.
#
# 实验 retired-surface-physical-cleanup (124e9a00) A3 两条防回潮规则：
#   Rule 1: manifest 中 scripts/*.sh 条目必须是 stub（含「已停用」退役标记 + exit 1），
#           被改回可执行真脚本 → fail。
#   Rule 2: 退役声明处（CLAUDE.md / Skill wake.md）引用的 scripts/*.sh 启动路径
#           必须已在矩阵 scripts 节登记，未登记 → fail 提醒补登记。

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

# Rule 1: deprecated 启动脚本必须是 stub（退役标记 + exit 1），改回真脚本即 fail
nonstub=0
while IFS= read -r path; do
  [[ "$path" == scripts/*.sh ]] || continue
  target="$ROOT/$path"
  if ! grep -q '已停用' "$target" || ! grep -q 'exit 1' "$target"; then
    echo "check-deprecated: DEPRECATED script is not a stub (missing retirement notice / exit 1): $path" >&2
    nonstub=$((nonstub + 1))
  fi
done < <(grep -E '^DEPRECATED: ' "$MANIFEST" | sed 's/^DEPRECATED: //' || true)

if [[ "$nonstub" -gt 0 ]]; then
  echo "check-deprecated: $nonstub non-stub deprecated script(s)" >&2
  exit 1
fi

# Rule 2: 退役声明处引用的启动路径必须在矩阵 scripts 节登记（登记单一真相 = 本矩阵）
registered="$(awk '
  /^## scripts/ { in_scripts = 1; next }
  /^## /        { in_scripts = 0 }
  in_scripts && match($0, /scripts\/[A-Za-z0-9._-]+\.sh/) { print substr($0, RSTART, RLENGTH) }
' "$MANIFEST" | sort -u)"

unregistered=0
declare -a declare_files=(
  "CLAUDE.md"
  "AGENTS.md"
  ".cursor/skills/map-project-collab/references/wake.md"
)
for rel in "${declare_files[@]}"; do
  decl="$ROOT/$rel"
  [[ -f "$decl" ]] || continue
  while IFS= read -r cited; do
    if ! grep -qxF "$cited" <<<"$registered"; then
      echo "check-deprecated: start path cited in $rel but not registered in matrix scripts section: $cited" >&2
      unregistered=$((unregistered + 1))
    fi
  done < <(grep -oE 'scripts/[A-Za-z0-9._-]+\.sh' "$decl" | sort -u || true)
done

if [[ "$unregistered" -gt 0 ]]; then
  echo "check-deprecated: $unregistered unregistered path(s)" >&2
  exit 1
fi

echo "check-deprecated: all manifest entries present; stub + registration rules pass"
