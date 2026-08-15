---
title: "M53 A2A 互操作映射（v0.11，实验性）"
acceptance:
  - "GET /api/v1/projects/{id}/agent-cards 返回项目全部 persona 的 Agent Card 列表"
  - "GET /api/v1/agents/{id}/agent-card 返回单 agent 卡片，字段符合 A2A Agent Card 核心结构（name/description/skills/capabilities）"
  - "卡片内容复用 persona 注册信息与 Skill 元数据（map-plugin.yaml），不新建存储表"
  - "docs/A2A-MAPPING.md 落地：MAP 对象 → A2A Task 概念映射表 + JSON 示例，标注协议版本基线"
  - "map work 的 Task 投影端点（GET /agents/{id}/tasks 或并入 work 投影）返回状态与映射表一致"
  - "docs/MCP.md 与 QUICKSTART 端口/安装/定位关系对齐，文档护栏测试通过"
  - "新增端点有 pytest 覆盖（含鉴权与 404），现有测试不回归"
evidence_keys:
  - "pytest 新增 agent-card / task-projection 测试全绿"
  - "pytest 快速门控全量回归通过"
  - "curl 实测 3 个 persona 的 agent-card 端点返回"
dependencies:
  - "M52（map-plugin.yaml 元数据）已完成（2f95f555）"
---

# M53 A2A 互操作映射

## 目标

按 docs/prd/v0.11.md §7 做只读「导出投影」：把 MAP 的既有对象（persona、话题轮次、实验生命周期、work 待办）映射为 A2A 概念并暴露只读端点，验证互操作可行性。不实现 A2A transport、不做跨组织发现。

## 改动范围

| 子项 | 内容 |
|------|------|
| M53A Agent Card | `server/api/` 新增 `GET /api/v1/projects/{id}/agent-cards` 与 `GET /api/v1/agents/{id}/agent-card`：name = agent_name、description = persona 职责一句话、skills = 该 persona 的 Skill 清单（读 map-plugin.yaml 元数据，服务端打包内置副本）、capabilities 按 persona 固化（host: 主持/创建实验；participant: 评论/执行；reviewer: 评审）；不新建存储表 |
| M53B Task 映射 | `docs/A2A-MAPPING.md`：topic round / experiment lifecycle / map work → A2A Task 状态映射表 + JSON 示例 + 协议版本基线标注；新增只读 Task 投影端点（每 persona 视角的 Task 列表），状态与映射表一致 |
| M53C MCP 对齐 | `docs/MCP.md` 与 QUICKSTART 对齐：端口（8001/3000）、安装示例、定位关系（本仓库 persona + CLI；外部项目 MCP 或 CLI 任一） |

## 实现顺序

1. M53A 端点 + 测试（Agent Card 是纯读投影，风险最低）
2. M53B 映射表文档 + Task 投影端点 + 测试
3. M53C 文档对齐 + 护栏验证
4. 全量回归 + 实测

## 风险与对策

- A2A 规范仍在演进：卡片只输出稳定核心字段（name/description/skills/capabilities），扩展字段不加；映射表标注协议版本基线，M53 标记实验性，不做兼容承诺
- Skill 元数据在服务端如何取：服务端读打包内置的 `cli/skills/*/map-plugin.yaml`（与 M52 同源），避免运行时依赖用户 workspace
- Task 投影与 work 投影重复：复用 `agent_work_service` 既有聚合，仅做 A2A 状态字段映射，不复制业务逻辑

## 评审建议落实（v2）

1. **鉴权边界**：agent-card 端点沿用项目内 Bearer token 鉴权（与 /agents/me 同级），404 与 403 行为写测试钉住；本实验不放开匿名读
2. **skills 列表粒度**：卡片 skills 输出 `id` + `name` 最小对（persona → Skill 多对多），不展开文件级细节
3. **映射表一致性**：Task 投影状态枚举与 `docs/A2A-MAPPING.md` 同源于常量模块（`server/domain/a2a_mapping.py`），护栏测试防文档与端点漂移
