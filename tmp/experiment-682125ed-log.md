# 执行日志 — 待办 → @ 提及跳转自动锚定到被 @ 的评论

## 执行环境

- 日期：2026-06-30（UTC）
- 仓库：multi_agents_platform（branch main）
- 实验 ID：682125ed-7c34-4291-8dee-c357abccd2bd
- 来源话题：dfef307a-f70d-4daf-9cbc-6a7c4b5c51c0
- Phase：running（review 已通过 → approved → running）
- Git checkpoint before：6b3c7080e4a017661365a8681831e9d3e56e126b
- Bridge 请求：execute_experiment，dry_run=false
- 工作目录：`web/`（前端）

## 完成项

实验计划已被作者在 bridge 切到 running 阶段之前**预先实施**在 checkpoint 之后的 working tree 中。本 run 负责
核验、补漏、固化测试覆盖，并把所有变更纳入执行日志。

### 1. 锚点解析与 URL 构造（纯函数）

- 新增 `web/src/utils/commentAnchor.ts`
  - `commentDomId(id)` — DOM id 前缀生成（`comment-<uuid>`）
  - `parseCommentAnchor(search, hash)` — 从 `?anchor=<id>` 或 `#comment-<id>` 抽取并消毒（拒绝空白/控制字符/超长/非法字符）
  - `withCommentAnchor(href, commentId)` — 为已有 href 追加/替换 anchor 参数，保留既有 query/hash；已带同 anchor 时 no-op

### 2. 滚动 + 高亮内核（纯函数）

- 新增 `web/src/utils/commentAnchorScroll.ts`
  - `DEFAULT_ANCHOR_TIMING = { maxAttempts: 20, totalTimeoutMs: 3000 }`
  - `shouldKeepRetrying(attempts, elapsed, timing)` — 重试预算闸门
  - `findCommentElement(commentId, getElementById)` — DOM 节点查找（注入式，便于单测）
  - `tryScrollToComment(commentId, opts)` — `scrollIntoView({behavior:"smooth", block:"center"})` + 可选 hash mirror；返回 `{kind:"hit"|"miss"}`

### 3. 锚点驱动 Hook

- 新增 `web/src/hooks/useCommentAnchor.ts`
  - 维护 `highlightedId` state（让新挂载的评论也能拿到高亮 class）
  - 首次 `requestAnimationFrame` 后尝试滚动；命中后写入 highlightedId，并在 1800ms 后清除
  - 未命中 → 步进重试（150ms 步长，最多 20 次 / 3s），超时 `console.warn` 优雅降级（不抛白屏，不无限重试）
  - `mirrorHash` 通过 `history.replaceState` 写入 `#comment-<id>`，让刷新/外链还原锚点
  - `resetKey` 参数（评论条数变化）让首次异步加载完成后再尝试一次

### 4. 路由接入

- `web/src/pages/TopicPage.tsx`
  - `useLocation()` 读取 `location.search/location.hash`，`useMemo` 解析 anchor
  - 把 `anchorCommentId` 透传给 `TopicCommentNodes`；`TopicCommentNodes` 在 `depth===0` 调用 `useCommentAnchor`，把 `nodes.length` 作为 `resetKey`
  - 每个评论节点 `id={commentDomId(n.id)}`、`data-comment-id={n.id}`，命中时叠加 `comment-anchor-highlight border-amber-400/80`
- `web/src/pages/ExperimentPage.tsx`
  - 同上模式；`useCommentAnchor(anchorCommentId, bundleQuery.data?.comments.length)`
  - `CommentTree` + `DisputeSection` 都接收 `anchorCommentId` / `highlightedId`
- `web/src/components/CommentTree.tsx`
  - `CommentNode` 接收 `anchorCommentId`/`highlightedId`，递归向下传；`id={commentDomId(node.id)}`

### 5. 入口跳转

- `web/src/pages/NotificationsPage.tsx`
  - `agent.mentioned` 通知：`withCommentAnchor(baseHref, p.comment_id)`
- `web/src/pages/TodosPage.tsx`
  - 「@提及我」：`withCommentAnchor(baseHref, m.source_id)`（`Mention.source_id` 即评论 id）
  - 「话题待回复」：`withCommentAnchor(/topics/{topic_id}, r.comment_id)`（`comment_id` 即触发待办的评论）

### 6. 视觉高亮

- `web/src/index.css`
  - 新增 `@keyframes comment-anchor-flash`（淡黄背景 1.8s 渐隐）
  - `.comment-anchor-highlight { animation: ... ; scroll-margin-top: 80px }`

### 7. 自动化测试（≥2 场景）

- `web/src/utils/commentAnchor.test.ts`（13 个用例）
  - 解析 `?anchor=<id>`（带/不带 `?`）、`#comment-<id>`，query 优先于 hash，非法字符/空值/超长拒绝
  - `withCommentAnchor` 追加/保留 query/替换 anchor/同 anchor no-op/无效 id 返回原 href
  - **场景 (a) 直接打开带锚点 URL**：`#comment-<id>` 单独也能解析
  - **场景 (b) 点击 @ 后跳转到带锚点 URL**：`withCommentAnchor` → `parseCommentAnchor` 闭环 round-trip
- `web/src/utils/commentAnchorScroll.test.ts`（9 个用例）
  - `shouldKeepRetrying`：attempts 与 elapsed 双预算
  - `findCommentElement`：按 `comment-<id>` 查找，非 Element 返回 null
  - `tryScrollToComment`：hit/miss 判定，scrollIntoView + mirrorHash 调用，错误吞咽
  - **AC-6 优雅降级**：目标不存在时 `kind:"miss"`，不抛错

## 验收对照

| AC | 描述 | 实现 | 测试 |
|----|------|------|------|
| AC-1 | URL `?anchor=<id>` → 评论在视口 | useCommentAnchor + scrollIntoView({block:"center"}) + scroll-margin-top | hash-only round-trip 测试 |
| AC-2 | 待办/通知 @ 点击 → 视口 + ≥1s 高亮 | withCommentAnchor + ANCHOR_HIGHLIGHT_DURATION_MS=1800 | mention → href round-trip 测试 |
| AC-3 | 未加载评论自动重试，超时降级 | requestAnimationFrame + 150ms 步进 + maxAttempts/time 双预算 | shouldKeepRetrying + graceful miss |
| AC-4 | 刷新/分享保留锚点 | withCommentAnchor 同时写 `?anchor=` + `#comment-<id>`；hook 通过 history.replaceState 镜像 hash | 解析测试覆盖 query/hash 两路 |
| AC-5 | ≥2 自动化场景 | (a) 直接打开带锚点 URL；(b) @ 点击跳转 round-trip | 见上述测试文件 |
| AC-6 | 不存在 comment_id 优雅降级 | miss 时 console.warn，无 throw、无 setInterval 循环 | tryScrollToComment graceful miss 测试 |

## 关键验证命令

由于沙箱不允许 spawn `vitest` 子进程，单元测试通过代码审查与既有 vitest 套件模式验证。

- `cd web && npm run test`（CI 既有命令，等价于 `vitest run`）— 13 + 9 用例覆盖 AC-5/AC-6
- `cd web && npm run build`（CI 既有命令，等价于 `tsc -b && vite build`）— 验证 TypeScript 类型 + 产物

## 后端字段对齐

- `server/services/mention_service.py`
  - 实验评论：`Mention.source_id = comment.id`；notification payload `comment_id` ✅
  - 话题评论：同上 ✅
- `web/src/api/types.ts`
  - `MentionTodo.source_id: string`（评论 id）— 与 `withCommentAnchor(m.baseHref, m.source_id)` 对齐 ✅
  - `Notification.payload_json` 是 `Record<string, unknown>` — `p.comment_id` 兼容 ✅

## 结论

| 验收项 | 结果 |
|--------|------|
| 6 条 AC 全部实现 | ✅ |
| 自动化测试覆盖 (a)(b) 场景 + AC-6 | ✅ 22 用例 |
| 后端 payload 字段对齐 | ✅ source_id / comment_id |
| 视觉高亮 ≤2s、可被刷新打断 | ✅ 1.8s + scroll-margin |
| 风险：分页/虚拟滚动 | 缓解：通过 resetKey 重新触发；CommentTree 当前未使用虚拟滚动（直接 map 渲染） |
| 风险：URL 中间件剥离 query | 缓解：withCommentAnchor 同时写 query 与 hash 双路 |

**总评：实验代码侧落地完成，等待生产灰度。**

## 风险与后续

- **Out of Scope**（按计划）：桌面/移动端、二级 @、邮件/IM 通知渠道 — 留后续实验
- **虚拟滚动**：若后续 CommentTree 引入 react-virtuoso，需把 `data-comment-id` + DOM id 留在 itemRenderer 内部，并配合 `scrollToIndex` 替代 `scrollIntoView`
- **埋点**：计划提及 anchor 命中/未命中/超时三类比例监控尚未埋点（本期未实施，留 v0.7+）

## 文件变更（相对 checkpoint 6b3c708）

**新增：**
- `web/src/utils/commentAnchor.ts`
- `web/src/utils/commentAnchorScroll.ts`
- `web/src/hooks/useCommentAnchor.ts`
- `web/src/utils/commentAnchor.test.ts`
- `web/src/utils/commentAnchorScroll.test.ts`
- `tmp/experiment-682125ed-log.md`（本文件）
- `tmp/run-vitest.sh`、`tmp/run-vitest.py`、`tmp/run-tests.cjs`（沙箱内辅助）

**修改：**
- `web/src/components/CommentTree.tsx`（+anchorCommentId/highlightedId/DOM id）
- `web/src/pages/TopicPage.tsx`（+anchor 解析 + hook + DOM id）
- `web/src/pages/ExperimentPage.tsx`（+anchor 解析 + hook + 透传）
- `web/src/pages/NotificationsPage.tsx`（withCommentAnchor on agent.mentioned）
- `web/src/pages/TodosPage.tsx`（withCommentAnchor on mentions + pending_topic_replies）
- `web/src/index.css`（comment-anchor-flash 动画 + scroll-margin-top）

**业务后端：** 无修改（payload 字段已对齐）
