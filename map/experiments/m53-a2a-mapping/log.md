---
experiment: 0b76b432-489a-42b2-bcc0-582bbe88a90d
title: "M53 A2A 互操作映射（v0.11，实验性）"
mode: standard
completed_at: 2026-08-15
---

# M53 执行日志

## 结果

三个子项全部完成：Agent Card 只读端点（项目级 + 单 agent）、A2A Task 语义映射表与 Task 投影端点、MCP 文档对齐。评审 3 条非阻塞建议（鉴权边界 / skills 粒度 / 映射表同源常量）全部在实施中落实。

## 实施 log

### M53A Agent Card 导出 ✅

- `server/domain/a2a_mapping.py`（新，单一事实源）：persona → 卡片内容注册表（description / skills / capabilities）+ Task 状态映射 + `A2A_PROTOCOL_BASELINE`（A2A draft 2025-03，实验性标注）
- `server/api/a2a.py`（新）：
  - `GET /api/v1/projects/{id}/agent-cards` — 项目全部成员卡片（协议基线 + total）
  - `GET /api/v1/agents/{id}/agent-card` — 单 agent 卡片，字段最小核心集（id/name/description/url/skills/capabilities/protocol）
- persona 提取按 `<project_key>-<persona>` 命名约定（`Agent.persona` property 仅识别 `multi-agent-platform-*` 前缀，卡片面向任意 bootstrap 项目故自行提取），未知 persona 降级为通用卡片
- skills 为 `id` + `name` 最小对（评审建议 2）；capabilities 按 persona 固化（host: topic.hosting / experiment.create / experiment.gatekeeping；participant: topic.comment / experiment.execute；reviewer: experiment.review）
- 鉴权（评审建议 1）：项目内 Bearer token；无 token 401、跨项目 403、未知 agent 404，均有测试钉住；未放开匿名读

### M53B Task 映射与投影 ✅

- `sdk/python/map_types/schemas/a2a.py`（新）：`AgentCardRead` / `A2ATaskRead` 等 5 个 schema，并入 `map_types.schemas` 导出
- `GET /api/v1/agents/{id}/tasks`：persona 视角只读 Task 投影——话题轮次（topic_progress + FS `fs_topic_progress_for_agent` 合并，open = working）+ 实验生命周期（`EXPERIMENT_TASK_STATE`：draft=submitted，review/approved/running/result_review=working，done=completed，cancelled=failed）
- 复用 `topic_progress_service` / `fs_source_service` / `project_service.list_experiments` 聚合，仅做状态字段映射，零业务逻辑复制
- `docs/A2A-MAPPING.md`（新）：映射表 + 双 JSON 示例 + 协议基线 + 定位边界；映射值与常量模块同源（评审建议 3），`TestMappingContract::test_docs_mapping_table_in_sync` 护栏防漂移

### M53C MCP 文档对齐 ✅

- `docs/MCP.md` 端口表修正：区分标准部署（8080/mcp）与本仓库 override（18081/mcp，宿主 8080 被占用的备用映射），与 QUICKSTART 服务地址表一致
- 头部定位声明明确「本仓库 persona + CLI；外部项目 MCP 或 CLI 任一」，互链 A2A-MAPPING.md
- `docs/INDEX.md` 登记 A2A-MAPPING.md

### 验收证据

| 验收项 | 证据 |
|--------|------|
| 新增测试全绿 | `pytest tests/test_a2a.py` → **11 passed**（卡片结构 / 401·403·404 / 状态映射 / 文档同源护栏） |
| 快速门控全量 | `pytest tests/` → **406 passed, 2 skipped**（395 基线 + 11 新增） |
| 文档护栏 | `pytest tests/test_docs_consistency.py` → **22 passed** |
| 实测（真实 app 对象） | 3 persona 卡片返回正确 skills/capabilities；无 token 401；tasks 投影协议字段正确 |
| lint | `ruff check` 新增文件 0 违规（--fix 自动修复 5 处 import 排序） |

## 风险

- 本地 dev server（uvicorn 后台进程）仍运行旧代码，wire 级 `/api/v1/*agent-card*`、`/tasks` 需重启后生效（TestClient 级验证与 wire 等效，同 M52 reissue 验证模式）
- A2A 规范演进风险由协议基线标注 + 实验性定位兜底；卡片仅输出稳定核心字段

## acceptance

- 全部 7 条 plan acceptance 通过：两个 Card 端点 ✅、复用元数据不新建存储 ✅、A2A-MAPPING.md 含映射表 + JSON 示例 + 版本基线 ✅、Task 投影状态与映射表一致（同源常量 + 护栏测试）✅、MCP/QUICKSTART 对齐 ✅、pytest 覆盖含鉴权与 404 ✅、快速门控无回归 ✅
