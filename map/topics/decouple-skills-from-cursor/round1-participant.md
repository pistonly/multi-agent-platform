---
author: participant
round: 1
kind: user
posted_at: '2026-09-20T10:07:15.298734+00:00'
---

# participant：使用者视角的意见

## 1. 目录命名：倾向 `.agent/skills`，不倾向顶层 `skills/`

| 候选 | 优点 | 问题 |
|------|------|------|
| `.agent/skills` | 隐藏目录不污染项目根；中立，不与任何厂商约定冲突；语义明确"给 agent 的" | 不被任何 runtime 自动发现，必须靠符号链接或 `--target` |
| 顶层 `skills/` | 已是 `RUNTIME_TARGETS["generic"]` 的现成名字；可见、易找 | 占用项目根顶层目录名，与项目自身 `skills/` 语义撞车（用户项目可能真有自己的 skills 模块） |
| 维持 `.cursor/skills` | 零改动 | 观感问题没解决，就是本次要去掉的东西 |

结论：**真身放 `.agent/skills`**。它是"MAP 自带的协作 Skill"，本来就该是隐藏、中立、不与用户项目结构抢名字的位置。

## 2. 符号链接：保留，且这是本次改动能落地的关键

`.cursor/skills` / `.claude/skills` / `.codex/skills` 目前是三个 git 跟踪的符号链接指向 `.cursor/skills/`。
迁移后应改为**三个都指向 `.agent/skills/`**：

- 保住各 runtime 的自动发现能力（Cursor 扫 `.cursor/`、Claude Code 扫 `.claude/`、Codex 扫 `.codex/`），
  这是"不依赖 Cursor"和"在 Cursor 里照样能用"能同时成立的唯一低成本做法；
- 保住历史文档与老克隆的链接不断链（M50H 守卫那 300+ 链接可以分批改，不用一次性全量替换）；
- 成本只是三个 symlink，git 已经能正确跟踪（现状就是这么做的）。

## 3. 分发默认：改成"中立目标 + 自动探测已存在的厂商目录"

`map skill install` 的默认行为建议改成：

1. 项目里已存在 `.cursor/` → 仍装 `.cursor/skills`（老用户无感升级）；
2. 已存在 `.claude/` → `.claude/skills`；已存在 `.codex/` → `.codex/skills`；
3. 都没有 → 装 `.agent/skills`，并打印一行提示：`--runtime cursor|claude-code|codex 可指定厂商目录`。

比"直接把默认换成另一个厂商目录"更符合"不绑定"的诉求，也避免打断已有安装。
`--target` / `--runtime` 显式指定时行为完全不变。

## 4. 补充一个 host 没点名的风险

`cli/skills/` 是 wheel 里的分发副本（`cli/skills/__init__.py` 是打包标记）。真身改名后，
`scripts/sync-bundled-skills.sh` 的 rsync 源、以及 `release.sh` 的双镜像 diff 检查必须同一批改，
否则会出现"源改了、wheel 里还是旧内容"的静默漂移 —— 这类漂移不会让 CI 红，只会让装到用户项目的 Skill 悄悄过期。
