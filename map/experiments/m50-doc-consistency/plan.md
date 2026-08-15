---
title: "M50 文档一致性修复包（v0.11）"
acceptance:
  - "QUICKSTART 中话题创建示例实际执行成功（--description 参数正确）"
  - "全仓库文档默认端口值唯一（8001），MAP_AGENT_PROMPT.md 与 docs/MCP.md 修正完成"
  - "docs/INDEX.md 与根 README.md 解析出的现行 PRD 版本与 docs/prd/README.md 一致（v0.10 主线 + v0.11 提案）"
  - "三个 Skill（host-checklist / participant-checklist / experiment-host）的内容路径约定统一为 map/，不再出现 docs/topics|experiments/ 新写指引"
  - "AGENTS.md 不再包含 Skill 已定义的规则表，行数明显下降，保留 persona 硬性规则与 waker 边界"
  - "孤儿文件 agents/openai.yaml 删除（cli/skills 与 .cursor/skills 两份拷贝）"
  - "tests/test_docs_consistency.py 通过，且人为注入漂移样例时测试失败（漂移护栏生效）"
evidence_keys:
  - "pytest tests/test_docs_consistency.py 全绿"
  - "pytest tests/test_docs_inventory.py 不回归"
  - "注入漂移样例（临时改端口/路径）后 pytest 失败，还原后恢复通过"
dependencies:
  - "none — 全部为文档与小测试改动，可独立先行合入"
---

# M50 文档一致性修复包

## 目标

按 docs/prd/v0.11.md §4（M50）修复评审发现的七类文档漂移，并为每类漂移建立 pytest 护栏，防止复发。

## 改动范围

| 子项 | 内容 |
|------|------|
| M50A | QUICKSTART Step 4 示例 `--body` → `--description` |
| M50B | 端口统一 8001（MAP_AGENT_PROMPT.md、docs/MCP.md） |
| M50C | 现行 PRD 指向统一（docs/INDEX.md、根 README.md），INDEX 版本表改链接 |
| M50D | Skill 内容路径统一 `map/`（host-checklist、participant-checklist、experiment-host） |
| M50E | AGENTS.md 薄索引化：规则表移除，替换为 Skill 链接 |
| M50F | 删除孤儿 agents/openai.yaml（两份拷贝） |
| M50G | 新增 tests/test_docs_consistency.py |

cli/skills/ 与 .cursor/skills/ 为镜像双份，M50D/M50F 同步修改。

## 验证

- pytest tests/test_docs_consistency.py tests/test_docs_inventory.py
- 注入漂移样例验证护栏失败路径
