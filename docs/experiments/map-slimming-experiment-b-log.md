# 实验 B 实施日志：UI 渲染改造（MAP 瘦身）

## 概述

在 Web UI 中实现本地 MD 文件内容的渲染，使用户在查看话题评论、实验计划和实验日志时，可以直接在 UI 中看到格式化的 Markdown 内容。

## 实施步骤

### 1. 重新生成 TypeScript 类型

运行 `scripts/gen_types.py`，从更新后的 Pydantic schemas 重新生成 `web/src/api/types.generated.ts`，前端类型现已包含 `file_path`、`excerpt`、`plan_file_path`、`log_file_path` 字段。

### 2. 新增 API 函数 + Hook

- `client.ts`：新增 `DocReadResponse` 接口和 `fetchDoc(projectId, path)` 函数，调用 `GET /projects/{projectId}/docs/read?path=...`
- 新建 `hooks/useDoc.ts`：TanStack Query hook，按文件路径获取 MD 内容，`staleTime: 30_000`，仅在 projectId 和 path 都有效时启用

### 3. 新增 FileBreadcrumb 组件

`components/FileBreadcrumb.tsx`：
- 显示文件相对路径（font-mono）
- "复制路径"按钮（clipboard API，2 秒后恢复）
- 轻量 inline 样式，不占额外空间

### 4. 改造 TopicPage 话题评论渲染

`pages/TopicPage.tsx`：
- 新增 `TopicCommentContent` 组件，当 `file_path` 存在时显示 `FileBreadcrumb` + 用 `useDoc` 获取 MD 文件内容并用 `MarkdownBody` 渲染
- 无 `file_path` 时保持原有行为（渲染 body）
- 系统评论仍使用 `SystemCommentBody` 渲染

### 5. 改造 PlanPanel

`components/PlanPanel.tsx`：
- 新增 `planFilePath?: string | null` prop
- 当 `planFilePath` 存在时：显示 `FileBreadcrumb` + 用 `useDoc` 获取文件内容 + `MarkdownBody` 渲染
- 不存在时：保持现有行为（渲染 `plan.content_md`）
- diff 模式优先级高于 file_path 渲染

### 6. 改造 LogPanel

`components/LogPanel.tsx`：
- 新增 `logFilePath?: string | null` prop
- 当 `logFilePath` 存在时：顶部显示 `FileBreadcrumb` + 用 `useDoc` 获取文件内容 + `MarkdownBody` 渲染
- 不存在时：保持现有行为（渲染各 log entry 的 `content_md`）

### 7. 更新 ExperimentPage

`pages/ExperimentPage.tsx`：
- 向 `PlanPanel` 传递 `planFilePath={experiment.plan_file_path}`
- 向 `LogPanel` 传递 `logFilePath={experiment.log_file_path}`

### 8. 修复 ReviewSummary 类型错误

`components/ReviewSummary.tsx`：
- 在 `STATUS_LABELS` 和 `STATUS_COLORS` 中添加 `closed` 状态定义，修复 TypeScript 编译错误

### 9. CommentTree 回滚

`components/CommentTree.tsx`：
- 因 `CommentTreeNode` 类型无 `file_path` 字段，回滚对实验评论的 file_path 支持，仅保留话题评论的支持

## 验证结果

- TypeScript 编译通过（`tsc --noEmit` 无错误）
- Web UI 构建成功（`npm run build` 无错误）
- 向后兼容：无 `file_path` 的评论/计划/日志保持原有渲染行为

## 改动文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `web/src/api/types.generated.ts` | 修改 | 重新生成，包含 file_path 等新字段 |
| `web/src/api/client.ts` | 修改 | 新增 fetchDoc 函数和 DocReadResponse 接口 |
| `web/src/hooks/useDoc.ts` | 新增 | TanStack Query hook |
| `web/src/components/FileBreadcrumb.tsx` | 新增 | 文件路径面包屑组件 |
| `web/src/components/PlanPanel.tsx` | 修改 | 支持 planFilePath 渲染 |
| `web/src/components/LogPanel.tsx` | 修改 | 支持 logFilePath 渲染 |
| `web/src/components/ReviewSummary.tsx` | 修改 | 添加 closed 状态 |
| `web/src/pages/TopicPage.tsx` | 修改 | 支持话题评论 file_path 渲染 |
| `web/src/pages/ExperimentPage.tsx` | 修改 | 传递 file_path 属性 |
