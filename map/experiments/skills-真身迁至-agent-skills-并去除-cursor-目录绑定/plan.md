---
title: "Skills 真身迁至 .agent/skills 并去除 Cursor 目录绑定"
acceptance:
  - "A1 真身迁移：`git mv .cursor/skills .agent/skills`；随后 `.cursor/skills`、`.claude/skills`、`.codex/skills` 三个路径均为指向 `../.agent/skills/` 的符号链接（git 跟踪 mode 120000，`git ls-files -s` 可核）；Skill 文件数 6 个不变"
  - "A2 双镜像一致：`scripts/sync-bundled-skills.sh` 的 rsync 源改为 `.agent/skills/`，执行后 `diff -r -x '__init__.py' .agent/skills/ cli/skills/` 为空，`cli/skills/__init__.py` 打包标记仍存在"
  - "A3 waker 契约升级：`cli/simple_waker.py:RUNTIME_CONTRACT_FILES` 五条路径改到 `.agent/skills/**`，`RUNTIME_CONTRACT_VERSION` 由 `simple-waker-runtime-contract-v3` 升到 `-v4`；`cli/wake_backend.py` 的镜像源同步改；`docs/MAP-SIMPLE-WAKER.md` 增加说明「契约版本变更需重启 waker」"
  - "A4 分发默认中立化：`cli/commands/skill.py` 默认目标改为「中立 + 自动探测」——项目已存在 `.cursor/` 装 `.cursor/skills`，已存在 `.claude/` 装 `.claude/skills`，已存在 `.codex/` 装 `.codex/skills`，都没有则装 `.agent/skills` 并打印 `--runtime`/`--target` 提示；显式 `--target`/`--runtime` 行为完全不变；`RUNTIME_TARGETS` 四选保持"
  - "A5 测试与文档：把硬编码 `.cursor/skills` 的测试断言改到新路径（tests/test_skill_versioning.py、test_skill_command_existence.py、test_simple_waker_drift_detection.py、test_cursor_wake_backend.py、test_red_line_clause.py、conftest.py）；README 与 docs 中的 `.cursor/skills` 链接批量替换为 `.agent/skills`（旧链接经符号链接仍可解析，可分批改）；`tests/test_docs_consistency.py` 的 M50H 链接守卫全绿"
  - "A6 版本纪律：6 个 Skill 的 `map-plugin.yaml` version 各递增一次（补丁位），便于用户判断拿到哪一版"
  - "A7 门禁：`ruff check` 0；pytest 全量绿（基线只增不减，0 failed）；`python scripts/gen_types.py` 无 diff（本次不涉及 schema）"
  - "A8 真机验收：起 `map-server`；waker 冷启动一次确认 Skill 能镜像到 `<runtime_home>/.claude/skills/`；`map skill install --target <临时目录>` 与 `--runtime cursor` 均成功；`map skill list` 正常输出 6 个 Skill"
  - "A9 边界（禁止越界）：保留 `.map/.cursor-env` 与 Cursor runtime 后端（那是可选 runtime，不是目录耦合）；wheel 分发结构不变（`cli/skills` 仍是唯一打包副本，`.agent/` 不得进入 sdist/wheel）；不引入新 server 端点 / DB 字段 / work kind"
evidence_keys:
  - "git 核证:`git ls-files -s .cursor/skills .claude/skills .codex/skills` mode 均为 120000 且指向 `../.agent/skills/`；`ls .agent/skills` 6 个 Skill 齐全"
  - "sync 核证:bash scripts/sync-bundled-skills.sh 输出 OK 且 diff 为空"
  - "grep 核证:grep -rn '\\.cursor/skills' --include='*.py' --include='*.sh' cli/ scripts/ 只剩注释/兼容性说明，无源路径依赖；RUNTIME_CONTRACT_VERSION 含 v4"
  - "pytest_summary:全量 pytest -q 0 failed，ruff check 0"
  - "实测输出:waker 冷启动日志含 startup_sync + skills_count=6；`map skill install --runtime cursor --target /tmp/<dir>` 成功写出 6 个 Skill 目录"
dependencies:
  - "话题 decouple-skills-from-cursor（Round 1 participant/reviewer 意见 + Round 2 host 收敛结论，已 close）"
  - "现有 cli/wake_backend.py:sync_runtime_skills 全量镜像机制（rmtree+copytree+孤儿清理）语义不得改变"
  - "现有 cli/simple_waker.py:RUNTIME_CONTRACT_FILES 哈希契约机制（本次只改路径 + 升版本）"
  - "Skill 分发面纪律：.agent/skills 与 cli/skills 双镜像必须一致，改内容必递增 map-plugin.yaml version"
  - "真机验收涉及 waker 重启，由监督者执行（契约版本升级需重启 waker 生效）"
---

# Skills 真身迁至 .agent/skills 并去除 Cursor 目录绑定

## 背景

MAP 不依赖任何 Agent Runtime，但 Skill 真身在 `.cursor/skills/`，`.claude/skills` / `.codex/skills`
只是指向它的符号链接，`map skill install` 默认也把 Skill 装进用户项目的 `.cursor/skills/`。
目录名与默认值都在暗示「必须装 Cursor」，与产品定位错位。话题 `decouple-skills-from-cursor` 已收敛出方案 B。

## 任务

真身迁到中立的 `.agent/skills/`，三个厂商目录降级为符号链接（保住各 runtime 自动发现与历史文档链接），
分发默认改为「中立 + 探测已存在厂商目录」，waker 运行时契约同步升版本。

## 实施步骤（每步可独立回滚）

### I1 真身迁移 + 符号链接重建

```bash
git mv .cursor/skills .agent/skills
rm .cursor/skills .claude/skills .codex/skills   # 删除旧链接（.cursor 下的真身已 mv 走）
ln -s ../.agent/skills/ .cursor/skills
ln -s ../.agent/skills/ .claude/skills
ln -s ../.agent/skills/ .codex/skills
git add -A .agent .cursor .claude .codex
```
注意：`.cursor/skills` 原是真身目录，mv 后需确认 `.cursor/` 下无残留；三个链接目标统一用 `../.agent/skills/`（与现状保持一致）。

### I2 双镜像同步脚本

`scripts/sync-bundled-skills.sh`：rsync 源 `.cursor/skills/` → `.agent/skills/`，注释里的说明同步改；
`scripts/release.sh` / `scripts/check-deprecated.sh` 中相关路径一并改。执行脚本确认 diff 为空。

### I3 waker 契约

- `cli/simple_waker.py`：`RUNTIME_CONTRACT_FILES` 五条改 `.agent/skills/<skill>/SKILL.md`；
  `RUNTIME_CONTRACT_VERSION` → `simple-waker-runtime-contract-v4`；注释中的 `.cursor/skills` 说明同步改。
- `cli/wake_backend.py`：`sync_runtime_skills` 的 source_root 参数默认值/调用点改到 `.agent/skills`。
- `docs/MAP-SIMPLE-WAKER.md`：写明契约 v3→v4，契约变更需重启 waker。

### I4 分发默认中立化

`cli/commands/skill.py`：`_DEFAULT_TARGET` 改为 `.agent/skills`；新增探测逻辑落到
`_resolve_target`（显式 `--target` 优先；否则按 `.cursor/` → `.claude/` → `.codex/` → `.agent/skills` 顺序探测项目根目录）；
安装完成提示语补一行 `--runtime cursor|claude-code|codex` 可指定厂商目录。
`RUNTIME_TARGETS` 增加/保留 `agent` 键映射 `.agent/skills`（保持与探测默认一致）。

### I5 测试 + 文档

按 A5 全量替换；跑 `pytest -q` 与 `ruff check`。

### I6 版本递增 + 真机验收

6 个 `map-plugin.yaml` version 补丁位 +1；按 A8 起 server + waker 冷启动 + install 到临时目录验收。

## 窄提交白名单

`^.agent/`、`^.cursor/`、`^.claude/`、`^.codex/`、`^cli/`、`^scripts/`、`^tests/`、`^docs/`、`^README.md`
（不得触碰 `web/`、`server/`、`map/` 内容与任何发版产物）
