# 结果评审：M53 A2A 互操作映射

## 总体评价

三个子项按 plan v2 落地，评审阶段提出的 3 条非阻塞建议全部落实并各有测试或文档锚点。验收证据完整：11 个新增测试 + 406 快速门控全量 + 22 文档护栏全绿 + 真实 app 对象实测。**建议接受**。

## 验收核对（对照 plan v2 acceptance）

1. **项目级 / 单 agent 卡片端点** ✅ — `GET /projects/{id}/agent-cards`（3 persona，total + protocol）与 `GET /agents/{id}/agent-card` 均落地并实测
2. **卡片字段核心结构** ✅ — id/name/description/url/skills/capabilities/protocol；skills 为 id+name 最小对（建议 2 落实）
3. **复用注册信息与元数据** ✅ — 无新建存储表；卡片内容来自 `a2a_mapping.PERSona_CARDS` 静态注册（与 map-plugin.yaml 清单同义），persona 按 `<project_key>-<persona>` 命名约定提取并降级兜底
4. **A2A-MAPPING.md** ✅ — 映射表 + 双 JSON 示例 + 协议基线标注（A2A draft 2025-03 experimental）
5. **Task 投影与映射表一致** ✅ — 状态枚举同源于 `server/domain/a2a_mapping.py`（建议 3 落实），`test_docs_mapping_table_in_sync` 护栏防漂移；experiment draft→submitted 有端到端断言
6. **MCP.md / QUICKSTART 对齐** ✅ — 端口表区分标准 8080 与 override 18081，与 QUICKSTART 一致；定位声明明确「外部项目 MCP 或 CLI 任一」
7. **pytest 覆盖** ✅ — 11 新增（卡片结构 / 401·403·404 / 状态映射 / 全 phase 映射完整性 / 文档同源），门控 406 全绿无回归

## 鉴权边界确认（建议 1 落实验证）

- 无 token 401、跨项目 403、未知 agent 404 各有独立测试；未引入匿名读路径——符合评审约定

## 结论

accept —— 全部验收项通过；dev server 需重启后 wire 级端点生效的风险已如实记录在 log 风险段。
