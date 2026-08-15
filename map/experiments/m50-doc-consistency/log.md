---
experiment: 42b942d4-0697-45f2-b4b7-f71529725bcb
title: "M50 文档一致性修复包（v0.11）"
mode: direct
completed_at: 2026-08-15
---

# M50 执行日志

## 结果

七个子项全部完成，验收标准逐条达成；文档漂移护栏（`tests/test_docs_consistency.py`）建立并通过反向注入验证。

## 子项明细

| 子项 | 改动 | 状态 |
|------|------|------|
| M50A | `docs/QUICKSTART.md` Step 4 话题创建示例 `--body` → `--description`，参数名与 CLI 实际定义对齐 | ✅ |
| M50B | 端口统一 8001：`MAP_AGENT_PROMPT.md`、`docs/MCP.md` 修正，全仓库文档默认端口值唯一 | ✅ |
| M50C | 现行 PRD 指向统一：`docs/INDEX.md` 版本表改链接、根 `README.md` 解析结果与 `docs/prd/README.md` 一致（v0.10 主线 + v0.11 提案） | ✅ |
| M50D | 三个 Skill（host-checklist / participant-checklist / experiment-host）内容路径约定统一为 `map/`，`cli/skills/` 与 `.cursor/skills/` 双份镜像同步修改 | ✅ |
| M50E | `AGENTS.md` 薄索引化：移除 Skill 已定义的规则表，保留 persona 硬性规则与 waker 边界，163 → 69 行 | ✅ |
| M50F | 孤儿文件 `agents/openai.yaml` 删除（`cli/skills/map-project-collab/` 与 `.cursor/skills/map-project-collab/` 两份） | ✅ |
| M50G | 新增 `tests/test_docs_consistency.py`：端口唯一性、PRD 版本指向、Skill 路径约定三类护栏 | ✅ |

## 验证证据

- `pytest tests/test_docs_consistency.py` 全绿；`tests/test_docs_inventory.py` 不回归。
- 反向验证：人为注入漂移样例（临时改端口 / 路径写法）后测试失败，还原后恢复通过 —— 护栏确实拦得住漂移。
- M50 完成时点全量相关测试 334 passed（含文档护栏、CLI 快照、既有回归面）。

## 遗留与移交

- 无阻塞遗留。M52 期间追加的 `auth.py` / `skill upgrade` 等改动已同步登记进 CLI 目录清单与 dry-run 分类护栏，文档一致性不受影响。
