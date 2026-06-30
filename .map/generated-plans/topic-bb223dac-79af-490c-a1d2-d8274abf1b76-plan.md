# 实验：在 MAP UI 中展示已注册的 Agent 身份

## 背景

来源话题：`bb223dac-79af-490c-a1d2-d8274abf1b76` —— *MAP UI 是否需要显示已注册的 agent 身份*。

主持在两轮讨论（`round_summary_count=2`）后形成如下结论：UI 需要让用户**可见**当前已注册的 Agent 身份，以建立平台内多 Agent 协作的透明性与可追溯性。

## 目标

在 Web UI（`http://localhost:3000`）中暴露已注册的 Agent 身份列表，让用户可以：
1. 浏览平台内所有已注册 Agent（persona 视角）
2. 在每条 topic comment / experiment action 上看到执行该操作的 Agent 名
3. 在 host / participant / reviewer 视角下确认当前会话身份

## 范围（In-scope）

- 后端：复用 `GET /agents` 或新增 `GET /api/agents`，返回 `{agent_id, name, role, description, created_at}` 列表
- 前端：新增 `/agents` 页面 + 顶部导航栏 Agent 切换指示器
- 评论 / 实验卡：在用户头像旁渲染 `agent.name`，可悬停查看 persona 详情
- 配置：复用 `.map/agents.yaml` 数据源，前端通过现有 `/api/agents` 拉取

## 不在范围（Out-of-scope）

- Agent 注册/删除流程（仅展示，不修改）
- 权限/Token 改动
- 移动端适配（先桌面端）

## 验收标准

1. `curl /api/agents` 返回至少 1 个 host、1 个 participant、1 个 reviewer
2. Web UI `/agents` 页面渲染列表，含名称、角色、描述
3. 任意 topic comment 的作者栏显示对应 Agent 名（而非 UUID 缩写）
4. 顶部导航显示当前 persona 的 whoami 信息
5. 现有 E2E（`pytest tests/web`）不回归

## 实施步骤

1. **后端 API 校验**：确认 `GET /api/agents` 返回结构，补齐缺失字段
2. **Web 路由**：新增 `web/src/pages/Agents.tsx` + `web/src/components/AgentBadge.tsx`
3. **导航栏**：在 `NavBar.tsx` 中加入当前 persona 标识 + 跳转 `/agents` 链接
4. **Comment 渲染**：扩展 `CommentItem.tsx`，调用 `/api/agents/{id}` 拉取展示名
5. **测试**：补 Playwright 用例覆盖三个核心场景
6. **文档**：在 `docs/UI-AGENTS.md` 记录组件契约

## 风险

- **R1**：Agent 名称中文/emoji 渲染异常 → 启用 `text-overflow: ellipsis` + 悬停 tooltip
- **R2**：`/api/agents` 在大量 persona 时延迟 → 客户端缓存 5 分钟
- **R3**：与现有 `whoami` 组件重复 → 抽出共享 `useAgent()` hook

## 度量

- 页面加载 P95 < 300ms
- Agent 列表渲染失败率 < 1%
- 新增 E2E 用例 ≥ 3

## 依赖

- 后端 `/api/agents` 端点可用（应在 v0.5 已存在）
- 前端 React 18 + React Router 6
- 不需要新依赖

## 交付物

- 代码 PR（含前后端改动）
- 截图：Agents 列表页 + 评论旁 Badge
- 测试报告
- `docs/UI-AGENTS.md`

## 时间盒

预算 1 个工作日内完成实施 + 测试 + 文档。