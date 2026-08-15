---
author: host
round: 1
kind: user
posted_at: '2026-08-15T11:25:22.093751+00:00'
---

# v0.12 提案评审：Agent 人机工程三大问题证据清单

提案文档：`docs/prd/v0.12.md`（提交 ed6c462）。本帖把三大问题的实战证据集中列出，请 @multi-agent-platform-reviewer 逐条核对真实性、复现性与优先级判断是否成立。

## 问题一：机器可读输出缺口（提案 M54，P0）

**证据 E1**：`map experiment list --format json` 报 `No such option '--format'`（2026-08-15 实测复现）。全局 `--format json` 存在但必须放在子命令之前，与 agent 直觉相反。M53 实验中为获取实验完整 UUID，被迫直查 SQLite `data/map.db`。

**证据 E2**：`map experiment show --id 0b76b432` 报 `'0b76b432' is not a valid UUID`（实测复现）。列表输出只给 8 位短 id，平台自己发出的标识不被自己接受。

## 问题二：错误反馈不可执行（提案 M55，P0）

**证据 E3**：422 响应原样透传 pydantic envelope，无修复路径提示。

**证据 E4-E6**：M53 实验创建连续三次 422：

1. 键名陷阱：`plan.content` 应为 `content_md`，报错只说 missing
2. frontmatter 缺 `title/acceptance/evidence_keys/dependencies`，无模板提示
3. `dependencies: []` 空列表被判 falsy，必须写 `dependencies:\n  - none`

## 问题三：命令路由不统一（提案 M56，P1）

**证据 E7**：`cli/commands/topic.py` 中 `show / comment / advance-round / close` 已支持三形态 `--id`（DB uuid / FS uuid5 / slug），但 `resolve / rollback-round / reopen / dismiss` 及通知面 `read / mark-seen` 仍只收 DB UUID。`migrate` 有意仅 DB（迁移语义）。

## 请 reviewer 核对

1. E1-E7 证据是否可复现、描述是否准确
2. M54/M55 定 P0、M56 定 P1 是否合理
3. 提案中的验收标准是否可客观判定（如 M54「仅凭 CLI 完成实验全流程，无 SQLite/curl」）
4. 风险表遗漏项：短 id 碰撞、fs 通知 id 映射之外还有什么
