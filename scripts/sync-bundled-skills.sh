#!/usr/bin/env bash
# 同步 .cursor/skills/（源头）→ cli/skills/（SDK pip 打包分发版）。
#
# 用途：每次修改 .cursor/skills/ 下的 Skill 后执行本脚本，保证
# `map skill install` 分发到第三方项目的内容与仓库内一致。
#
# 注意：
# - cli/skills 是 Python 包目录（pyproject.toml 的 packages.find 与
#   package-data "cli.skills" 依赖其 __init__.py 存在），rsync 时必须排除，
#   否则 --delete 会删掉它导致 pip 打包丢失全部 Skill 文件。
# - .claude/skills 与 .codex/skills 是指向 ../.cursor/skills 的符号链接，
#   无需（也不能）单独同步。
set -euo pipefail
cd "$(dirname "$0")/.."

rsync -a --delete --exclude='__init__.py' .cursor/skills/ cli/skills/

# 防御性校验：打包标记必须存在
if [ ! -f cli/skills/__init__.py ]; then
  echo "ERROR: cli/skills/__init__.py 丢失，pip 打包将丢失 Skill 文件" >&2
  exit 1
fi

# 防御性校验：内容一致（排除打包标记后不允许有差异）
if ! diff -r -x '__init__.py' .cursor/skills/ cli/skills/ >/dev/null; then
  echo "ERROR: .cursor/skills/ 与 cli/skills/ 仍存在差异" >&2
  diff -r -x '__init__.py' .cursor/skills/ cli/skills/ | head -20 >&2
  exit 1
fi

echo "OK: cli/skills/ 已与 .cursor/skills/ 同步（保留 __init__.py 打包标记）"
