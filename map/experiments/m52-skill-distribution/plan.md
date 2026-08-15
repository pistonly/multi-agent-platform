---
title: "M52 Skill 分发与接入可靠性（v0.11）"
acceptance:
  - "每个 Skill 目录含 map-plugin.yaml（version / requires / runtime_targets），install 写入版本清单"
  - "已存在目标不再静默 skip，而是提示当前版本与可用版本"
  - "map skill list --installed 显示已装版本与内置版本 drift"
  - "map skill upgrade 先输出 diff 摘要（新增/删除/变更文件数与行数）再覆盖，--force 保留整目录覆盖语义"
  - "--runtime cursor|claude-code|codex|generic 映射到对应目录，默认 cursor 向后兼容"
  - "install 成功后输出装后自检命令（persona whoami 链路验证）与 troubleshooting 定位锚点"
  - "map auth reissue --name --key 重签发 token：旧 token 401、新 token 正常、写回 .map/agents.local.yaml"
  - "bootstrap 409（agent 名已存在）错误信息包含 reissue 命令"
  - "QUICKSTART 自动化协作路径补 waker 启动说明（Step 6）"
  - "新增功能均有 pytest 覆盖，现有 skill/auth 相关测试不回归"
evidence_keys:
  - "pytest tests/cli/test_skill*.py（或等价新测试文件）全绿"
  - "map skill install --runtime claude-code 在临时目录写入 .claude/skills/ 且输出自检提示"
  - "map auth reissue 实测：重签发后旧 token 调用 401、新 token whoami 成功"
dependencies:
  - "none — 与 M50 可并行；不动 M51（事实源收敛）范围内的话题路由代码"
---

# M52 Skill 分发与接入可靠性

## 目标

按 docs/prd/v0.11.md §6（M52）补齐 Skill 分发的版本化、多 runtime 目标与接入凭据恢复能力，消除「装完即孤儿、token 丢即死锁」两类接入断点。

## 改动范围

| 子项 | 内容 |
|------|------|
| M52A | Skill 版本化：map-plugin.yaml（version/requires/runtime_targets）、install 写清单、list --installed drift、upgrade diff 预览 |
| M52B | 多 runtime 目标：--runtime cursor/claude-code/codex/generic 目录映射 |
| M52C | map auth reissue：服务端重签发端点 + CLI 命令 + bootstrap 409 提示 + troubleshooting 文档更新 |
| M52D | QUICKSTART 补 waker 启动说明（自动化协作路径） |

## 实施要点

- skill.py 现有 skip/force 逻辑保留，新增版本清单与 runtime 映射层
- reissue 服务端复用现有 agent token 生成逻辑，不新增表结构（更新 agents 表 token 字段）
- CLI 侧 reissue 写回 .map/agents.local.yaml 并提示备份

## 验证

- pytest 新增测试 + 现有 skill/auth 测试
- 实机验证 reissue 前后 token 行为
