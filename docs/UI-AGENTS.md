# UI：已注册 Agent 身份展示

> 实验落地文档（实验 ID：`7a1f89e9-cf3b-469f-b156-71f40fdfee88`）

本文档描述 MAP Web UI 中如何展示已注册的 Agent 身份，包括组件契约、缓存策略和扩展指南。

## 1. 目标

让用户在 Web UI 上能：

1. 浏览当前可见的 Agent 列表（含角色、所属项目、ID、创建时间）。
2. 在每条评论、话题、决策、待办上看到执行该操作的 Agent 名称（不再是 UUID 缩写）。
3. 在 Layout 顶部快速跳转到 Agents 页面，并通过显示名确认当前会话身份。

## 2. 数据源

### 后端：`GET /api/v1/agents`

- 路径前缀：`/api/v1/agents`
- 鉴权：必须登录（任意角色）
- 查询参数：
  - `role`：`admin` | `agent` —— 可选过滤
  - `project_id`：UUID —— 可选过滤
- 返回：`list[AgentRead]`，字段为 `{id, name, role, project_id, project_key, created_at}`
- 可见性：
  - **admin**：看到全部 Agent
  - **普通 agent**：看到全部 admin + 绑定到同一项目的 agent

### 缓存

- 通过 React Query 缓存，`staleTime = 5 分钟`
- 调用入口：`web/src/hooks/useAgents.ts`
- 单一数据源：comment / topic / todo 等组件都通过 `useAgents().byId` 解析 Agent，避免每条评论各发一次请求。

## 3. 组件契约

### `AgentBadge`

`web/src/components/AgentBadge.tsx`

| Prop | 类型 | 说明 |
|------|------|------|
| `agentId` | `string` | 必填，作者/操作者的 Agent ID |
| `fallbackName` | `string \| null` | 当缓存未命中时使用（通常是后端 join 出来的 `author_name`） |
| `fallbackRole` | `AgentRole \| null` | 当缓存未命中时使用 |
| `className` | `string?` | 追加样式 |
| `showRole` | `boolean?` | 是否显示角色徽标，默认 `true` |
| `compact` | `boolean?` | 紧凑模式（不显示角色徽标） |

行为：

- 先从 `useAgents().byId[agentId]` 查询
- 若命中，使用缓存中的 `name` / `role`
- 若未命中，回退到 `fallbackName` 或 `UUID 前 8 位 + …`
- 鼠标悬停展示完整描述（name/role/project_key/id/created_at）

### `useAgents`

`web/src/hooks/useAgents.ts`

```ts
const { agents, byId, isLoading, error, refetch } = useAgents(opts?);
```

- `agents`：`Agent[]`
- `byId`：`Record<string, Agent>` —— 用 `agentId` 取展示名
- `isLoading` / `error` / `refetch`：同 React Query

### `AgentsPage`

`web/src/pages/AgentsPage.tsx`

- 路由：`/agents`
- 提供：当前会话信息（whoami）、角色过滤、名称/项目/ID 搜索、表格视图
- 数据刷新：`刷新` 按钮调用 `queryClient.invalidateQueries({ queryKey: ["agents"] })`

## 4. 接入点（已实现）

| 场景 | 文件 | 变更 |
|------|------|------|
| 顶部导航 | `web/src/components/Layout.tsx` | 新增 `Agents` 链接 + 当前 Agent 名点击跳转 |
| 路由注册 | `web/src/App.tsx` | 新增 `/agents` 路由 |
| 实验评论（CommentTree） | `web/src/components/CommentTree.tsx` | 评论作者渲染为 `AgentBadge` |
| 话题评论 / 创作者 / 结论 / 行动项 | `web/src/pages/TopicPage.tsx` | 所有 Agent 字段改为 `AgentBadge` |
| 待办：@提及 / 话题待回复 | `web/src/pages/TodosPage.tsx` | 提及来源使用 `AgentBadge` |

## 5. 验收对照

| 验收项 | 实现位置 |
|--------|----------|
| `GET /api/v1/agents` 返回 host / participant / reviewer 三类 | 后端 `server/api/agents.py:list_agents` + 测试 `tests/test_agents_list.py` |
| `/agents` 页面渲染列表 | `web/src/pages/AgentsPage.tsx` |
| 评论作者显示 Agent 名 | `web/src/components/CommentTree.tsx` + `web/src/components/AgentBadge.tsx` |
| 顶部导航显示当前 persona | `web/src/components/Layout.tsx`（右上角 Agent 名 + `Agents` 链接） |
| 缓存降低请求数 | `web/src/hooks/useAgents.ts`（staleTime 5 分钟，单点拉取） |

## 6. 后续可选改进（未在本次范围）

- 给 `Agent` 模型增加 `description` 字段，补齐 plan 文档中提到的 description
- 增加 `/agents/{id}` 单点查询端点，减少列表请求体
- E2E 测试：Playwright 覆盖三个核心场景
- Agent 头像首字母支持 emoji / 中文渲染
