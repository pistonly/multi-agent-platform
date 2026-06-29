# 目标

P0 交付后，补齐主持工作流 **P1** 缺口：**话题评论通知定向主持** + **实验 topic_id 软校验与 CLI/MCP 提示**。

**源话题**：[P1] 主持工作流增强（`e5553c84-25ef-4b81-9a86-e75746077bee`）

**背景共识**（map-agent-2 校准 + 主持采纳）：
- 议题3（topic_id 软校验 + `--topic`）+ 议题1（通知定向 creator）→ **本实验范围**
- 议题2（@主持 direct reply 例外）→ **stretch，本实验不做**

---

# Part A — 话题评论通知定向 `creator_agent_id`

## A1. `server/services/notification_service.py` / event handler

`topic.comment.created` 事件：默认仅通知话题 `creator_agent_id`（排除 actor），替代当前广播全项目 Agent。

保留 `enqueue_for_agents` 供 @mention 等定向场景；mention 逻辑不变。

## A2. 验收

- [ ] 非主持 Agent 评论 → 仅主持收到通知
- [ ] 主持自己评论 → 无自通知
- [ ] @mention 仍定向被 @ 的 Agent
- [ ] 集成测试覆盖

---

# Part B — 实验 `topic_id` 软校验与 CLI/MCP `--topic`

## B1. 创建实验时软提示

当 `topic_id` 为空且项目存在 open 话题时：
- API 返回 **201** 但响应/日志含 `warnings: ["no_topic_id"]`（或等价 metadata）
- 不阻断 legacy 无话题实验

## B2. CLI / MCP

- `map experiment create` 增加 `--topic-id`（已有）文档化；无 topic 时 stderr 打印软提示
- MCP `create_experiment` 同步：`topic_id` 可选 + 软提示字段

## B3. 验收

- [ ] 有 open 话题、未绑 topic_id → 软警告可见
- [ ] 显式 `--topic-id` → 无警告
- [ ] 现有 strict 校验（closed topic、非主持开实验）行为不变

---

# 非目标

- @主持 direct reply 例外规则（议题2）
- Web UI 大改
- P0 已交付项

---

# 风险

- 通知收窄后其他 participant 可能「漏看」→ 仍可通过 @mention 与 Web 话题列表跟进
