---
title: "实验 B：UI 渲染改造（MAP 瘦身）"
acceptance:
  - "Web UI 中带 file_path 的评论渲染本地 MD 文件内容，而非 body stub"
  - "文件路径面包屑显示正确，支持复制路径和编辑器打开链接"
  - "无 file_path 的评论保持原有渲染行为（向后兼容）"
  - "PlanPanel 在有 plan_file_path 时渲染本地文件内容"
  - "LogPanel 在有 log_file_path 时渲染本地文件内容"
  - "TypeScript 类型重新生成，包含 file_path/excerpt/plan_file_path/log_file_path"
evidence_keys:
  - "types.generated.ts 包含 file_path 等新字段"
  - "浏览器中查看带 file_path 的评论显示 MD 文件内容"
  - "面包屑路径显示且复制功能正常"
dependencies:
  - "实验 A 已完成：file_path/excerpt/plan_file_path/log_file_path 字段存在"
  - "GET /docs/read API 端点已可用"
---

# 实验 B：UI 渲染改造（MAP 瘦身）

## 目标

在 Web UI 中实现本地 MD 文件内容的渲染，使用户在查看话题评论、实验计划和实验日志时，可以直接在 UI 中看到格式化的 Markdown 内容，而非在 UI 和编辑器之间来回切换。

依赖实验 A 已完成的 `file_path`/`excerpt`/`plan_file_path`/`log_file_path` 字段和 `GET /docs/read` API 端点。

## 改动范围

### 1. 重新生成 TypeScript 类型

运行 `scripts/gen_types.py` 从更新后的 Pydantic schemas 重新生成 `web/src/api/types.generated.ts`，使前端类型包含 `file_path`、`excerpt`、`plan_file_path`、`log_file_path` 字段。

### 2. 新增 API 函数 + Hook

- `client.ts`：新增 `fetchDoc(projectId, path)` 函数，调用 `GET /projects/{projectId}/docs/read?path=...`
- 新建 `hooks/useDoc.ts`：TanStack Query hook，按文件路径获取 MD 内容，`staleTime: 30_000`

### 3. 新增 FileBreadcrumb 组件

`components/FileBreadcrumb.tsx`：
- 显示文件相对路径（如 `docs/topics/map-slimming/round1-host.md`）
- "复制路径"按钮（clipboard API）
- "在编辑器中打开"链接（`vscode://file/<absolute-path>`）
- 轻量 inline 样式，不占额外空间

### 4. 改造 CommentNode（实验评论）

`components/CommentTree.tsx` 中的 `CommentNode`：
- 当 `node.file_path` 存在时：
  - 顶部显示 `<FileBreadcrumb path={node.file_path} />`
  - 用 `useDoc(node.file_path)` 获取文件内容
  - 加载中显示 skeleton，加载失败 fallback 到 `node.body`
  - 用 `<MarkdownBody content={docContent || node.body} />` 渲染
- 当 `node.file_path` 不存在时：保持现有行为（`<MarkdownBody content={node.body} />`）

### 5. 改造 TopicPage 话题评论渲染

`pages/TopicPage.tsx` 中的 `TopicCommentNodes`：
- 与 CommentNode 相同的 file_path 逻辑
- 显示 `<FileBreadcrumb>` + `useDoc` 获取内容
- 评论列表中 `excerpt` 作为 fallback 摘要显示

### 6. 改造 PlanPanel

`components/PlanPanel.tsx`：
- 新增 `planFilePath?: string | null` prop
- 当 `planFilePath` 存在时：
  - 显示 `<FileBreadcrumb path={planFilePath} />`
  - 用 `useDoc(planFilePath)` 获取文件内容
  - 用 `<MarkdownBody>` 渲染（统一渲染组件，替换直接 `ReactMarkdown`）
- 当不存在时：保持现有行为（渲染 `plan.content_md`）
- `ExperimentPage.tsx` 传递 `experiment.plan_file_path` 到 PlanPanel

### 7. 改造 LogPanel

`components/LogPanel.tsx`：
- 新增 `logFilePath?: string | null` prop
- 当 `logFilePath` 存在时：
  - 顶部显示 `<FileBreadcrumb path={logFilePath} />`
  - 用 `useDoc(logFilePath)` 获取文件内容
  - 用 `<MarkdownBody>` 渲染（统一渲染组件）
- 当不存在时：保持现有行为（渲染各 log entry 的 `content_md`）
- `ExperimentPage.tsx` 传递 `experiment.log_file_path` 到 LogPanel

## 不改动

- 后端 API（实验 A 已完成）
- CLI 命令（实验 A 已完成）
- 数据库 schema（实验 A 已完成）

## 验证

- 启动 Web UI，创建带 `file_path` 的评论，验证 UI 渲染本地 MD 文件内容
- 验证文件路径面包屑显示和"在编辑器中打开"链接
- 验证无 `file_path` 的评论保持原有渲染行为（向后兼容）
- 验证 PlanPanel/LogPanel 在有/无 file_path 时的渲染
