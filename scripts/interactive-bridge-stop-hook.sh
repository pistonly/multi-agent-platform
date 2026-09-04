#!/usr/bin/env bash
# 交互会话桥接 Stop hook（实验 db97aeac）：回合结束时把 MAP 待办提醒注入当前会话。
#
# 安装（Claude Code，.claude/settings.json）：
#   "hooks": { "Stop": [{ "hooks": [{ "type": "command",
#     "command": "<repo>/scripts/interactive-bridge-stop-hook.sh host" }] }] }
#
# 参数 $1 = persona 短名（host/participant/reviewer）；省略时由
# `map bridge hook` 读 .map/config.yaml 的 default_persona。
# 桥接层只读 map work 平台事实 + 去重/投递，不写 MAP；失败静默 exit 0。
set -uo pipefail

PERSONA="${1:-}"
if [ -n "$PERSONA" ]; then
  exec map --persona "$PERSONA" bridge hook
fi
exec map bridge hook
