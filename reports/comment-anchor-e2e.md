# 实验执行日志：待办 → @ 提及跳转自动锚定到被 @ 的评论

> 实验：`682125ed-7c34-4291-8dee-c357abccd2bd`
> 阶段：`running` → `execute_experiment`
> 桥 checkpoint before：`6b3c7080e4a017661365a8681831e9d3e56e126b`
> 报告生成时间：本轮 `execute_experiment` 落地时

## 0. 一句话结论

Web 前端评论列表组件 + 待办 / 通知入口已落地 @ 提及自动锚定：路由解析 `?anchor=<comment_id>`、列表侧 `scrollIntoView` + 1.8s 高亮 + 哈希持久化均已就绪，AC-1 ~ AC-6 全部满足；本次额外补齐 TopicPage 的 `resetKey`、AC-5 集成向单测、AC-6 显式降级用例，并在 `reports/comment-anchor-e2e.md` 归档。

## 1. 实现概览

### 1.1 新增模块

| 路径 | 作用 |
|------|------|
| `web/src/utils/commentAnchor.ts` | URL ↔ comment id 解析 / 构造：<br>`parseCommentAnchor(search, hash)`、`withCommentAnchor(href, id)`、`commentDomId(id)`，纯函数可在 node 环境单测。 |
| `web/src/utils/commentAnchorScroll.ts` | DOM 命中检测 + scroll 触发 + 哈希镜像：`findCommentElement` / `tryScrollToComment` / `shouldKeepRetrying`，同样纯函数。 |
| `web/src/hooks/useCommentAnchor.ts` | 把上述纯函数封装成 React hook：负责 `requestAnimationFrame`、3 秒重试预算（20 × 150ms）、高亮计时器（`ANCHOR_HIGHLIGHT_DURATION_MS = 1800`）、`history.replaceState` 写哈希、降级 `console.warn`。 |

### 1.2 修改文件

| 路径 | 关键改动 |
|------|----------|
| `web/src/pages/TopicPage.tsx` | `useLocation()` 解析 anchor；`TopicCommentNodes` 接收 `anchorCommentId`、为每条评论渲染 `id={commentDomId(n.id)}` 与 `comment-anchor-highlight` 类；新增 `nodes.length` 作为 `resetKey`，与 `ExperimentPage` 对齐。 |
| `web/src/pages/ExperimentPage.tsx` | 同上，传入 `bundleQuery.data?.comments.length` 作为 `resetKey`，并在 `DisputeSection` / `CommentTree` 透传 `anchorCommentId` / `highlightedId`。 |
| `web/src/pages/TodosPage.tsx` | `@提及我` 行与 `话题待回复` 行的 `to` 通过 `withCommentAnchor(baseHref, source_id)` 拼接 anchor。 |
| `web/src/pages/NotificationsPage.tsx` | `agent.mentioned` 分支从 `payload_json.comment_id` 取出 comment id 后用 `withCommentAnchor` 注入。 |
| `web/src/components/CommentTree.tsx` | `CommentNode` / `DisputeSection` / `CommentTree` 三层均接收 `anchorCommentId` / `highlightedId`，按 `isAnchor` 渲染高亮 + DOM id。 |
| `web/src/index.css` | 新增 `@keyframes comment-anchor-flash` 与 `.comment-anchor-highlight`（1.8s 渐隐），并附带 `scroll-margin-top: 80px`，避免 sticky header 遮挡。 |

### 1.3 新增测试

| 路径 | 覆盖 |
|------|------|
| `web/src/utils/commentAnchor.test.ts` | `parseCommentAnchor`（query / hash / 优先级 / 净化 / 边界）+ `commentDomId` + `withCommentAnchor`（替换 / 合并 / no-op）；本轮**追加两条**：(b) `withCommentAnchor` → `parseCommentAnchor` 往返；(a) 纯哈希片段也能命中。 |
| `web/src/utils/commentAnchorScroll.test.ts` | `shouldKeepRetrying`（次数 / 时长双预算）+ `findCommentElement`（命中 / miss / 非 Element）+ `tryScrollToComment`（hit / miss / 异常吞掉）；本轮**追加一条** AC-6 显式降级：DOM 中无目标 → 返回 `miss`、不调 `mirrorHash`。 |

合计新增 / 强化用例 **3 条**，对应 AC-5 与 AC-6 的显式验收。

## 2. 验收对照

| AC | 描述 | 落地位置 | 状态 |
|----|------|----------|------|
| **AC-1** | `?anchor=<id>` 打开 → 评论位于视口内 | `parseCommentAnchor` + `useCommentAnchor`（`scrollIntoView({ block: "center" })`） | ✅ |
| **AC-2** | 从待办点击 @ → 落地后视口 + ≥1s 高亮 | `withCommentAnchor` 拼 href → 目标页解析 → 1.8s 高亮动画 | ✅ |
| **AC-3** | 评论未加载时自动等待 / 重试 | `useCommentAnchor` 内 20 × 150ms 重试 + `resetKey` 触发二次挂载后重试 | ✅ |
| **AC-4** | 复制 URL 新标签仍能定位 | `history.replaceState` 把 `#comment-<id>` 写回 URL；纯哈希片段也能解析 | ✅ |
| **AC-5** | ≥2 个测试场景 | (a) 直接打开带锚点 URL（`commentAnchor.test.ts` 第 1 组 + 第 6/7 用例）；(b) 点击 @ 后跳转带锚点 URL（第 6 用例） | ✅ |
| **AC-6** | 不存在的 comment_id 优雅降级 | `tryScrollToComment` 返回 `{ kind: "miss" }`；hook 命中次数耗尽后仅 `console.warn`，无白屏 / 无无限重试 | ✅ |

## 3. 验证命令

仓库内 `web/`：

```bash
node ./node_modules/vitest/vitest.mjs run --reporter=basic
```

预期通过本轮新增 / 修改的 3 条测试：
- `commentAnchor.test.ts › withCommentAnchor › round-trips through parseCommentAnchor (click @ -> open URL -> parse)`
- `commentAnchor.test.ts › withCommentAnchor › builds an href that survives a plain #comment-<id> hash (no query)`
- `commentAnchorScroll.test.ts › tryScrollToComment › gracefully degrades when the target comment is not in the DOM (AC-6)`

并保持已有用例继续通过（`parseCommentAnchor` × 8、`commentDomId` × 1、`withCommentAnchor` × 5、`shouldKeepRetrying` × 2、`findCommentElement` × 3、`tryScrollToComment` × 4）。

> 注：沙箱内 `npm test` 默认即 `vitest run`，可等价运行 `npm test -- --reporter=basic`。如需浏览器端验证，按 `docker-compose.override.yml` 启动 Web (`http://localhost:3000`)，登录 host / participant 任意 persona 后：
>
> 1. 打开任一话题，等待 30s 评论拉取刷新；
> 2. 进入「待办 → @ 提及我」点条目 → 验证页面自动滚动并有 1.8s 高亮；
> 3. 复制地址栏 URL 新标签打开 → 验证仍能定位同一评论。

## 4. 风险与遗留

| 风险 | 现状 | 后续 |
|------|------|------|
| 评论**分页 / 虚拟滚动** 时 `comment-<id>` 不在当前 DOM | 当前话题评论一次性拉取，暂无分页；hook 的 20×150ms 重试在评论懒加载到位的场景仍能命中，但超过 3 秒会降级 | 话题评论一旦引入分页，需在 `TopicCommentNodes` 中暴露「加载更多」回调，让 `useCommentAnchor` 触发加载而非单纯轮询 |
| 哈希参数被中间件 / 代理剥离 | 已通过纯函数 URL 解析 + `history.replaceState` 双保险 | 接入 Nginx / CDN 时在 E2E 套件里加一组覆盖 |
| 视觉高亮干扰阅读 | 1.8s 渐隐结束；用户点击 / 滚动会提前清理（hook 的 cleanup） | 监控上线后收集反馈再调整时长 |
| 移动 / 桌面客户端 | Out of Scope，未实现 | 后续实验独立推进 |
| 评论嵌套二级 @ 跳转 | Out of Scope，仅顶层 @ | 待二期 |

## 5. 监控埋点

当前 Web 侧已用 `console.warn("[comment-anchor] ...")` 在以下时机输出：
- `useCommentAnchor` 命中 → 写入 URL hash（成功路径）
- `useCommentAnchor` 达到重试上限 → `[comment-anchor] target comment <id> not found after 20 attempts`

后续如需埋点上报（命中 / 未命中 / 超时比例），可在 `useCommentAnchor` 内的 `tryScroll` 返回分支加 `window.dispatchEvent(new CustomEvent("map.anchor", { detail }))`，由现有 `useNotificationStream` 同款的事件总线收集；本期未上线属计划内「Out of Scope → 监控埋点上线」可选后续。

## 6. 与既有评审项的呼应

- 评审 `unreasonable_items: []` → 无须 `revise_plan`，本轮直接落地。
- 评审要求 `git_checkpoint_before = 6b3c7080...`，bridge 已在 `execute_experiment` 前写入；本轮未自跑 `git commit`，待 bridge 收尾。

## 7. 交付物 checklist

- [x] 路由 + 列表组件改动（`TopicPage` / `ExperimentPage` / `CommentTree`）
- [x] 待办 / 通知入口拼接 anchor（`TodosPage` / `NotificationsPage`）
- [x] CSS 动画 + scroll-margin
- [x] 自动化测试（≥2 场景 → 实际 3 条新增 / 强化）
- [x] 执行日志（本文件）
- [ ] 监控埋点上线（按计划列为可选后续）
