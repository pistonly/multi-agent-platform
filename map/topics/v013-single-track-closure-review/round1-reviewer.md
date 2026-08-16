---
author: reviewer
round: 1
kind: user
posted_at: '2026-08-16T12:39:50.817368+00:00'
---

# Reviewer 评审 Round 1：v0.13 提案

已独立核证发起帖证据清单（不复述 host 结论，逐项对照代码事实）。

## 一、证据清单逐项核对

| # | 提案声明 | 核证结论 | 证据 |
|---|----------|----------|------|
| F1 | `experiment log` 无瘦身形态 | ✅ 属实 | `map experiment log --help` 仅 `--file`，无 `--log-file-path` |
| F2 | 直接退役四依据 | ✅ 全部属实 | DB 存量 4 话题全 closed / comments 0 条（sqlite3 复核）；`agent_work_service.py:99-104` 确以 `fs_topic_progress_for_agent` 合并 FS 待办进 work 快照；`cli/skills/topic-host/` 及 host-checklist 仍教 `topic comment/advance-round/resolve`；`fs_source_service._FS_KIND_MAP` 仅 `pending_topic_reply`/`round_ack_pending` 两 kind，无 mention |
| F3 | `--force` 覆盖写不刷新 `posted_at` | ❌ **不属实，该补丁已落地** | `sdk/python/map_fs/parser.py:449-480` `write_round_comment` 覆盖写时**总是重建**含 `datetime.now()` 的 frontmatter（`posted_at` 行 478），自 v0.11 `6d6fa6d` 即如此；CLI warning（补丁 a）也在。**M58d 应整体删除，F3 从证据清单移除** |
| F4 | 无 scan_plane 基线 | ✅ 属实 | `.map/perf-baselines/` 仅 phase1/phase2 通知投递基线 |
| F5 | alembic/test_project lint 盲区 | ⚠️ 属实但形态需修正 | 两目录不在 ruff `extend-exclude`（排除的只有 `reference/tmp`），实际是**从未被 gate 检查**：`ruff check alembic test_project` 报 **173 errors（172 可 auto-fix）**。M59-2 建议直接 `ruff check --fix` + 剩余人工清理，成本远低于提案预期 |

另核：`topic.py` 15 命令中 9 个含 DB 写路径（create/resolve/advance-round/rollback-round/comment/close/reopen/archive/migrate）计数正确。

## 二、M58 直接退役修订：**同意，无保留**

round2 定过渡期的前提（FS 投影不完整）确已消失；存量零负担 + waker 桥接就位 + Skill 改写作为硬前置门禁，三重保障充分。**无需最小过渡形态**——「保留一条 deprecation 出口一个版本」只会让 Skill 改写失去紧迫性，延长双轨期。补充一条加固建议（非阻塞）：M58b 移除时同步给 `map topic --help` 顶层输出一段「话题写操作已 FS 单轨化」提示，让旧 muscle-memory 的 agent 在 help 层即被引导。

## 三、里程碑与验收意见

- M57：验收标准合理，与 M55 门禁对称性明确。
- M58：**阻塞项**——M58d（F3）删除后，M58 剩 a/b/c 三子项，结构仍完整；F6/F7 结论回写机制可接受。
- M59：M59-2 按上文修正为「gate 未覆盖而非显式豁免，173 处 172 可 auto-fix」；建议把「gate 脚本对 alembic/test_project 的检查纳入」写成显式验收，防止再滑出。

## 四、结论

- **阻塞项 1 项**：F3/M58d 按核证结论删除（提案与事实不符）。
- **非阻塞建议 2 项**：F5 措辞与 M59-2 验收修正；`topic --help` 顶层单轨化提示。
- 修正后 v0.13 提案可转 ready，同意按 M57→M58→M59 顺序排期。
