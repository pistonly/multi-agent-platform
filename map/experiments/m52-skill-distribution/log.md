---
experiment: 2f95f555-28b9-4a8f-b6c9-c13a77dbbf6d
title: "M52 Skill 分发与接入可靠性（v0.11）"
mode: direct
completed_at: 2026-08-15
---

# M52 执行日志

## 结果

四个子项全部完成：Skill 版本化、多 runtime 安装目标、token 自助重签发、QUICKSTART 接入文档补全。「装完即孤儿、token 丢即死锁」两类接入断点闭环。

## 子项明细

### M52A Skill 版本化 ✅

- 10 份 `map-plugin.yaml` 清单（5 个 Skill × `cli/skills/` + `.cursor/skills/` 镜像），字段：`version: 0.11.0`、`requires: 0.11`、`runtime_targets`。
- `cli/commands/skill.py` 重写（~470 行）：
  - `skill list --installed`：输出 Bundled / Installed / Drift 漂移表（`up-to-date` / `update available (X → Y)` / `installed newer` / `unknown`）。
  - `skill install`：安装时显示版本；目标已存在不再静默 skip，而是提示已装与内置版本并指向 `map skill upgrade`。
  - `skill upgrade`（新命令）：先输出 diff 摘要（新增/删除/变更文件数与 +N/-N 行数）再覆盖；`--force` 保留整目录覆盖语义；未安装时提示改用 install。
  - 装后自检：输出 `map --persona host persona whoami` 验证链路 + `.map/config.yaml` 缺失 WARN + troubleshooting 锚点。

### M52B 多 runtime 安装目标 ✅

- `--runtime cursor|claude-code|codex|generic` 分别映射 `.cursor/skills/`、`.claude/skills/`、`.codex/skills/`、`skills/`，默认 cursor 向后兼容。
- `--target` 显式路径优先级高于 `--runtime`（文档化）。

### M52C map auth reissue ✅

- 服务端：`POST /api/v1/bootstrap/reissue`（`TokenReissueRequest/Response`），`reissue_agent_token` 原子轮换 `api_token_hash`——旧 token 立即失效；信任模型与 bootstrap 同源（project_key 即所有权证明）。
- `bootstrap_service` 两处 409（agent 名已存在）错误信息追加 `map auth reissue` 恢复指引。
- SDK：`reissue_map_token()` 解析 project_key（参数 → `.map/config.yaml`），成功后写回 `.map/agents.local.yaml` 对应 persona（按 agent 名反查 persona，未命中则按 agent 名直接落键）；裸 404（旧 server 无路由）降级为可行动的 ValueError。
- CLI：`map auth reissue --name [--key]`，`cli/commands/auth.py` 新 sub-app；JSON / human 双输出。
- dry-run 分类：`auth reissue` 登记为写命令（服务端改写 token）。

### M52D QUICKSTART 接入文档 ✅

- 新增 Step 3.5：`map skill install`（四种 runtime 示例 + list/upgrade 版本化命令）。
- waker 章节补状态文件（`.map/simple-waker-state-*.json`）、日志（`.map/waker-logs/`）、session 转录（`.map/runtime-waker-sessions/`）说明。
- `.map/agents.local.yaml` 丢失 FAQ 重写：以 `map auth reissue` 恢复路径为首选项。

## 验证证据

- 测试全绿：
  - 快速门控全量 `pytest`：**372 passed, 2 skipped**（含新增 `test_skill_versioning.py` 25 例、`test_auth_reissue.py` 5 例、`test_bootstrap.py` 9 例含 5 个 reissue 用例）。
  - `tests/test_skill_install.py`（被 slow 反选，单独跑）：**16 passed**。
  - `test_bootstrap.py::test_reissue_rotates_token_and_revokes_old`：真实 FastAPI TestClient 级验证 —— 旧 token → 401、新 token → 200 且 agent_id 不变、`previous_token_revoked=True`。
- 护栏登记齐备：`auth_app` / `skill upgrade` 补进 dry-run 分类与 `_APP_VAR_TO_PATH`；`cli/commands/auth.py` 补进目录清单快照；两模块加入 fast-gate 白名单。
- 实机验证：
  - 临时目录 `map skill install --runtime claude-code`：5 个 Skill 落 `.claude/skills/`，逐项显示 `(N file(s), v0.11.0)`，输出自检命令 + config 缺失 WARN + troubleshooting 锚点。
  - 仓库根 `map skill list --installed`：漂移表正确显示 5 个 Skill 均 `0.11.0 up-to-date`。
  - 真实 CLI 对旧版 server 调 `map auth reissue`：输出可行动降级信息（"server does not support token reissue (needs MAP server >= v0.11)…"），无堆栈泄漏。

## 修复过程中发现的问题

- 裸 404 判别：FastAPI 缺失路由默认体 `{"detail": "Not Found"}` 含 "not found" 子串，原「子串包含」判别失效；改为精确匹配默认 literal 后 `MAPNotFoundError` 与友好降级正确分流。
- SQLAlchemy `Project.project_key` 字段名（非 `key`）；`server/services/auth.py` 模块名（非 auth_service）。

## 遗留与移交

- 本机 API 进程（uvicorn, :8001）仍运行改动前代码，wire 级 reissue 需按常规部署路径重启后生效（TestClient 级轮换行为已验证等价）；QUICKSTART 的 FAQ 已覆盖旧 server 场景的降级提示。
- `map-plugin.yaml` 的 `requires` 字段当前仅声明未强校验（install 不因 CLI 版本不符而拒绝）；建议后续 milestone 加最低 CLI 版本检查。
