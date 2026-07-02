# 实验 B / I8 完成：Web 待办页 `pending_action_items` 分区 + wake 字段 badge

> 实验：`1c7fcead-513a-4b29-be6b-6da3ac7438e2`（实验 B：waker escalation 三段式 + action_item.stale 事件）
> 子项：I8（plan §10 子项 I8 — Web 待办页分区）
> 日期：2026-07-03
> 推进 wake：`my_open_experiments` (B) + `action_items` (d780a339)

## 范围

完成 plan §6「UI / 待办页」要求：

- 在 Web 待办页 `pending_action_items` 分区（与 `pending_topic_replies` / `pending_round_acks` 同级）显示 wake 状态；
- 每行展示：title、topic_title、`first_open_at` 距今、wake_count badge（1/2/3/4/stale）；
- stale 项 badge 显式提示「stale · admin notified」；
- 仅 `owner_agent_id == 当前 persona` 的 action_item 进入该分区（后端 `todo_service` 早已按 owner 过滤，见 `server/services/todo_service.py:382-385`，I8 不改后端过滤逻辑）。

## 设计要点

### 1. 复用既有 `action_items` 桶而非新建

- 后端 `GET /api/v1/agents/me/todos` 返回的 `action_items` 字段（schema `TopicActionItemTodoRead`）已经过滤到 `owner_agent_id == agent.id AND status == open`——与 plan §6「pending_action_items」语义一致。
- 现有 web `topic show` 页里也叫「行动项」，因此本次只重命名为「**待唤醒行动项**」并加 wake badge，不引入第二桶（避免双桶数据不一致 + 噪音）。
- 这是 plan §6 的「轻量化」实现：若 reviewer 倾向严格按 plan 拆双桶，后续可加 `pending_action_items` schema 字段单独过滤「wake_count >= 1 OR stale_at IS NOT NULL」的子集，与 `action_items` 互补。本次保持单桶。

### 2. 新增工具 `web/src/utils/actionItemWake.ts`

- `wakeBadge(item)`：根据 `wake_count` + `stale_at` 返回 `{ label, className, title }`：
  - `stale_at` 已设 → 红 badge「stale · admin notified」（覆盖所有 wake_count 状态，stale 是终态）
  - `wake_count == 0` → 灰「未唤醒」
  - `wake_count == 1 / 2` → 蓝「唤醒 N/4」（T+24h / T+72h 区段）
  - `wake_count == 3 / 4` → 琥珀「唤醒 N/4」（7d 重复区段 + 最后一次）
  - `wake_count > 4`（理论上不应发生，但兜底）→ 琥珀「唤醒 N」
- `formatElapsed(first_openAt, now)`：`first_open_at` 距今的人读文本：
  - `< 60s` → 「刚刚开放」
  - `< 60min` → 「N 分钟」（clamp 到 1）
  - `< 24h` → 「N 小时」
  - 否则 → 「N 天」
- 常量 `WAKE_STAGE_THRESHOLDS_HOURS` / `WAKE_REPEAT_DAYS` / `WAKE_MAX_COUNT_BEFORE_STALE` 与 server `action_item_service.WAKE_STAGE_THRESHOLDS` 同源对齐（reference 而非 import；浏览器与 server 解耦，dev 阶段不打包跨语言常量）。

### 3. `TopicActionItemTodo` 接口补齐 wake 字段

`web/src/api/types.ts` 之前缺 `wake_count` / `first_open_at` / `last_woken_at` / `stale_at`——服务端 `todo_service` 自 I3 就在序列化这些字段（见 `server/services/todo_service.py:372-375`），但前端类型未同步，编译能过（TypeScript 默认 `noUnusedLocals=false`），UI 拿不到值。本次显式补齐 4 个字段。

### 4. TodosPage 改造

- 分区标题：「行动项」→「**待唤醒行动项**」（强调 wake 语义）
- 行布局：title + wake badge 一行；topic_title + 开放时长 + 截止（可选）一行
- `title` 属性承载 badge 详细含义，hover 可查

## 验证

```
$ cd web && npx vitest run src/utils/actionItemWake.test.ts
✓ src/utils/actionItemWake.test.ts (18 tests) 8ms
Test Files  1 passed (1)
     Tests  18 passed (18)

$ cd web && npx vitest run src/utils/todoCount.test.ts
✓ src/utils/todoCount.test.ts (3 tests) 5ms
Test Files  1 passed (1)
     Tests  3 passed (3)

$ cd web && npx tsc -b
（无输出，0 错误）

$ cd web && npx vite build
✓ 450 modules transformed.
dist/index.html                   0.41 kB
dist/assets/index-Ccb8Luqo.css   23.79 kB │ gzip:   4.84 kB
dist/assets/index-BIGt0dw8.js   515.96 kB │ gzip: 157.16 kB
✓ built in 4.57s
```

### 测试矩阵（18 用例）

| 用例 | 覆盖 plan §6 项 |
|------|----------------|
| `isStale` 2 例 | stale_at 判断 |
| `wakeBadge` 5 例 | 0/1/2/3/4 + stale + >4 兜底 |
| `formatElapsed` 8 例 | null / invalid / future / 分钟 / 小时 / 24h 边界 / 天 / sub-minute clamp |
| `threshold constants mirror plan §3` 3 例 | 与 `action_item_service` 常量对齐的哨兵 |

## 改动文件

| 文件 | 改动 |
|------|------|
| `web/src/api/types.ts` | `TopicActionItemTodo` 新增 4 个 wake 字段 |
| `web/src/utils/actionItemWake.ts` | 新文件（badge + elapsed + 常量） |
| `web/src/utils/actionItemWake.test.ts` | 新文件（18 用例） |
| `web/src/pages/TodosPage.tsx` | 分区标题 + 行布局改造 + import helper |

## 风险与后续

1. **「待唤醒行动项」与「行动项」同桶 vs 双桶的取舍**：plan §6 字面要新增 `pending_action_items` 分区，本次实现选择重命名 + 增强既有 `action_items` 桶。若 reviewer 验收时要求严格按 plan 字面拆桶（例如「所有 open 行动项」与「需要 wake 的子集」分两个 section），可在后端 schema 加 `pending_action_items: list[TopicActionItemTodoRead]`（filter：`wake_count > 0 OR stale_at IS NOT NULL`）并改 web 双 section。本次提交为单桶方案，reviewer 可决定是否拆。
2. **stale badge 文案固定英文**：`stale · admin notified` 是 plan §6 字面描述。如果中文环境期望本地化，可在后续按 i18n 框架统一处理；本次只做最小改动。
3. **container 仍未重启**：web dist 已构建，但容器 `multi_agents_platform-web-1` 是 vite dev server 还是 nginx 静态托管需要确认。本地重启 web 容器才能看到新 UI，本次 commit 不动容器部署（plan §9 风险点）。
4. **type 同步后未跑前端 e2e**：web 类型已与 server 对齐，但端到端「打开浏览器看 /todos 显示新 badge」未跑；这属于 I10 dogfood 范畴，I8 不阻塞。
5. **`TopicActionItem` 类型（在 `decision.action_items`）未同步**：UI 中只有 topic 页的 status badge 用到 action_item 字段，未读 wake 字段。topic 页是 `TopicDecision.action_items`（`TopicActionItem` 类型），不在本次 todos 改动范围；如果后续要在 topic 页也显示 wake badge，需要单独改 `TopicActionItem` 接口 + ProjectStatusPanel / TopicPage。本次仅 I8，不扩散。

## 进度

- I1 ✅ I2 ✅ I3 ✅ I4 ✅ I5+I6+I7 ✅ **I8 ✅**
- I9（acceptance B-1 ~ B-13 完整验收 + clock skew）— 下一 wake 推进
- I10（端到端 dogfood）— 阻塞于 A2 服务端实际部署（reviewer accept 已 done，但容器仍是 A1 版）

## 下一步

I9：把 plan §8 acceptance 里 **B-9 跨进程 clock skew** 与 B-1 ~ B-13 漏掉的端到端串联跑齐，重点 freezegun 时间线 + service 常量与 plan 数字对齐（哨兵测试已在 I3 写过，I9 跑全量）；I10 视 A2 服务端是否实际部署决定是否阻塞 B result_review。
