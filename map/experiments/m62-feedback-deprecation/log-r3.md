# 执行日志 r3（host）：返工——补 M62-c 收口 commit（reviewer 驳回项闭环）

## 驳回项（结果评审 a1eb330d，7 条 review item：6 passed / 1 failed）

M62-c「Skill 三层」执行了但未收口进 git：前收口 commit 的 `git add` 路径列表（cli/ server/ sdk/ tests/ web/src/ docs/ map/）**漏列 `.cursor/`**——

1. `.cursor/skills/` 源头三文件修改游离工作区，HEAD 层面与已清理的 `cli/skills/` 分发源**漂移**（基于 HEAD 跑 sync-bundled-skills.sh 会回写旧内容或防御校验失败）
2. commit message 自报「Skill 三层完成」与 git 事实不符——证据链自报完成、交付未完成

## 返工动作（窄，一个 commit + 本 log）

- **commit `chore(skills): 补 M62-c 收口——.cursor 源三文件提交`**：SKILL.md（description + 死链行）、bootstrap-troubleshooting.md:55（退役引导）、host-checklist.md:113（指引改退役说明）
- `web/vite.config.js`（vitest coverage 配置，与 M62 无关）按窄 commit 原则**不夹带**，留工作区
- 复核：`bash scripts/sync-bundled-skills.sh` 防御校验 **OK**（源/分发源零漂移）；`git status` 仅剩无关 vite.config.js

## 教训记录（日志纪律）

窄 commit 的边界判定必须以 `git status --short` 全量对照 acceptance 清单，而非依赖 `git add` 路径枚举——路径枚举漏项时工作区残留不会被察觉，而 commit message 会把「文件系统已改」误报为「已交付」。reviewer 的「git 为证，非文件系统表象」是正确的验收判据。
