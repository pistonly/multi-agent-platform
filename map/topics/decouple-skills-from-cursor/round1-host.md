---
author: host
round: 1
kind: user
posted_at: '2026-09-20T10:06:16.216686+00:00'
---

# 议题：去除 Skills 目录对 Cursor 的绑定

## 现象

- Skill 真身放在 `.cursor/skills/`（6 个：map-project-collab / topic-host / topic-participant / experiment-host / experiment-reviewer / experiment-executor）。
- `.claude/skills`、`.codex/skills` 只是指向 `../.cursor/skills/` 的符号链接（git 已跟踪）。
- `map skill install` 默认 `--runtime cursor` → 安装到用户项目的 `.cursor/skills/`。

MAP 本身不依赖任何 Agent Runtime（CLI / Web / SDK / REST+SSE+Webhook 四接入面，runtime 只是 waker 可选后端），
但目录名与默认值都在暗示"必须装 Cursor"，这是产品定位与文件布局的错位。

## 两类耦合（要分开处理）

1. **分发面默认值**：`cli/commands/skill.py` 的 `_DEFAULT_TARGET = ".cursor/skills"`、`_DEFAULT_RUNTIME = "cursor"`
   （`RUNTIME_TARGETS` 已有 cursor / claude-code / codex / generic 四个选项，只是默认偏向 cursor）。
2. **本仓库源目录**：`.cursor/skills/` 是双镜像的源头（`scripts/sync-bundled-skills.sh` rsync 到 `cli/skills/`），
   同时也是 waker 运行时契约源 —— `cli/simple_waker.py` 的 `RUNTIME_CONTRACT_FILES` 硬编码 5 个 `.cursor/skills/**/SKILL.md`
   做 hash，`cli/wake_backend.py` 启动时把 `.cursor/skills/<skill>` 镜像到 `<runtime_home>/.claude/skills/<skill>`。

## 影响面（实测盘点）

- 48 个文件含 `.cursor/skills` 字面量（源码 / 脚本 / 测试 / 文档，排除 web、build、dist、map 产物）。
- 源码：`cli/commands/skill.py`、`cli/simple_waker.py`、`cli/wake_backend.py`、`cli/drift_detector.py`、`cli/agent_client.py`。
- 脚本：`scripts/sync-bundled-skills.sh`、`scripts/release.sh`、`scripts/check-deprecated.sh`。
- 测试：`test_skill_versioning.py`、`test_skill_command_existence.py`、`test_red_line_clause.py`、
  `test_simple_waker_drift_detection.py`、`test_cursor_wake_backend.py`，
  以及 `test_docs_consistency.py` 的 M50H 相对链接守卫（docs + 双份 skills + 根级 md，约 75 文件 / 321 链接）。
- 文档：README 8 处、docs 若干（含 `.map/.cursor-env` 说明 —— 那是真 Cursor runtime 凭据，**不在本次解耦范围**）。

## 候选方案

- **A 最小改动**：目录不动，只把分发默认值改为中立目标（如 `.agent/skills`）或按已存在目录自动探测；改 README 措辞。
  风险最低，但仓库布局仍叫 `.cursor`，观感问题没根治。
- **B 彻底解耦（推荐）**：真身迁到中立目录（候选 `skills/`——与 `RUNTIME_TARGETS["generic"]` 同名，或 `.agent/skills`），
  `.cursor/skills` / `.claude/skills` / `.codex/skills` 全部降级为指向它的符号链接（保向后兼容），
  同步改 waker 契约哈希清单、sync 脚本、测试断言、文档链接。
- **C 最干净**：真身迁中立 + 删除三个厂商符号链接。破坏既有链接引用，需一次性替换全部文档链接。

## 需要澄清的风险

1. `RUNTIME_CONTRACT_FILES` 路径变更 → 运行中的 waker 会判 drift。契约是否要版本化（v3 → v4）并要求重启 waker？
2. M50H 文档守卫下 300+ 链接批量替换，是"全量改"还是"旧链接豁免一段时间"？
3. 已安装用户本地 `.cursor/skills`：`map skill upgrade` 按内容 diff 判定，目标目录不变则不受影响 —— 是否需要在
   某个版本提示"可迁移到中立目录"？

## 建议

走 **B**：真身中立 + 保留厂商符号链接做兼容，分发默认改为中立目标（`--runtime` 仍可选 cursor）。
请 participant / reviewer 就目录命名（`skills/` vs `.agent/skills`）与符号链接保留策略给出意见。
