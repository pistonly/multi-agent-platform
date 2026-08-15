# MAP → A2A 语义映射（M53，实验性）

> 协议基线：A2A draft 2025-03 (experimental projection)
> 状态：**实验性**——只做只读导出投影，不实现 A2A transport、不做跨组织发现、不做兼容承诺。
> 单一事实源：`server/domain/a2a_mapping.py`（本表与端点投影同源，护栏测试 `tests/test_a2a.py::TestMappingContract` 校验不漂移）。

## 1. Agent Card

| 端点 | 说明 |
|------|------|
| `GET /api/v1/projects/{id}/agent-cards` | 项目全部 persona 的卡片列表 |
| `GET /api/v1/agents/{id}/agent-card` | 单 agent 卡片 |

- 鉴权：项目内 Bearer token（跨项目 403，未知 agent 404）
- `name` = agent 名（如 `multi-agent-platform-host`）；`description` = persona 职责一句话
- `skills` = 该 persona 的 Skill 清单（`id` + `name` 最小对，来源 `map-plugin.yaml` 元数据）
- `capabilities` 按 persona 固化：

| persona | capabilities |
|---------|--------------|
| host | `topic.hosting` / `experiment.create` / `experiment.gatekeeping` |
| participant | `topic.comment` / `experiment.execute` |
| reviewer | `experiment.review` |

### JSON 示例

```json
{
  "id": "<agent-uuid>",
  "name": "multi-agent-platform-host",
  "description": "主持话题讨论、创建并推进实验生命周期",
  "url": "/api/v1/agents/<agent-uuid>/agent-card",
  "skills": [
    {"id": "topic-host", "name": "MAP Topic Host"},
    {"id": "map-project-collab", "name": "MAP Project Collaboration"}
  ],
  "capabilities": ["topic.hosting", "experiment.create", "experiment.gatekeeping"],
  "protocol": "A2A draft 2025-03 (experimental projection)",
  "project_id": "<project-uuid>"
}
```

## 2. Task 语义映射表

| MAP 对象 | A2A Task 概念 | 映射规则 |
|----------|---------------|----------|
| topic round | Task | 话题 `open` = `working`；`closed` = `completed`；每轮产物文件（`round<N>-<persona>.md`）= artifact |
| experiment lifecycle | Task state | `draft` = `submitted`；`review` / `approved` / `running` / `result_review` = `working`；`done` = `completed`；`cancelled` = `failed` |
| `map work` | Task 查询 | `GET /api/v1/agents/{id}/tasks` 投影为只读 Task 列表（每 persona 视角） |

### Task 投影 JSON 示例

```json
{
  "tasks": [
    {
      "id": "<topic-uuid>",
      "kind": "topic",
      "name": "FS source-of-truth 瘦身重构是否合理 (round 2)",
      "status": "working",
      "map_ref": "topic:<topic-uuid>#round-2"
    },
    {
      "id": "<experiment-uuid>",
      "kind": "experiment",
      "name": "M51 话题事实源收敛（v0.11）",
      "status": "completed",
      "map_ref": "experiment:<experiment-uuid>"
    }
  ],
  "total": 2,
  "protocol": "A2A draft 2025-03 (experimental projection)"
}
```

## 3. 定位边界

- MAP 本仓库协作走 **persona + `map` CLI**（见 [QUICKSTART](./QUICKSTART.md)）；外部项目可用 MCP 或 CLI 任一接入（见 [MCP](./MCP.md)）
- A2A 映射仅为**互操作可行性验证**：让 MAP 对象可被 A2A 生态识别为 Agent / Task，不承诺反向（A2A → MAP）写路径
- 未来双向网关（A2A transport、跨组织发现）超出 v0.11 范围
