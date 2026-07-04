# Experiment: 给 @ 提及待办加 dismiss/已读出口

## 背景

`/todos` 页面 `@提及我` 段把 `Mention` 表所有行无差别塞进去,用户即使点了链接读了、被 @ 的评论只是"已读/共识确认"类(无需要求回复),也无法把它从待办里清掉——只有再发评论才有可能,且当前实现不会自动清。结果是 noise 越堆越多。

参见 server/services/todo_service.py:204 `mention_rows = mention_service.list_mentions_for_agent(...)` 直接全部返回;`Mention` 模型(server/domain/models.py:383)无 `dismissed_at` 字段;server/api 下无 dismiss 路由;web/src/pages/TodosPage.tsx:35 无操作按钮。

## 目标

为 mentions 待办提供明确的"已读/忽略"出口,让用户在无需求回复时也能清理噪声,同时不破坏审计可追溯性。

## 方案

### 数据层
- `Mention` 加 `dismissed_at: Mapped[datetime | None]`
- alembic 迁移 `018_mention_dismissed_at.py`:
  - `add_column("mentions", "dismissed_at", DateTime(timezone=True), nullable=True)`
  - `create_index("ix_mentions_mentioned_agent_id_dismissed_at", ["mentioned_agent_id", "dismissed_at"])`

### 服务层
- `mention_service.list_mentions_for_agent` 默认加 `where(Mention.dismissed_at.is_(None))`,提供 `include_dismissed=False` 参数
- 新增 `mention_service.dismiss_mention(db, agent, mention_id) -> Mention | None`(校验 mentioned_agent_id == agent.id)
- 新增 `mention_service.dismiss_all_for_agent(db, agent) -> int`

### API(server/api/agents.py)
- `POST /agents/me/mentions/{mention_id}/dismiss` → `{"id": "...", "dismissed_at": "..."}`
- `POST /agents/me/mentions/dismiss-all` → `{"dismissed": <count>}`
- 两个端点都鉴权当前 agent(只能清自己的)

### 自动 dismiss(同 thread 回复)
在 comment_service.create_comment / topic_service.create_topic_comment 写完评论后,自动 dismiss 该 thread 内 `@` 该新评论作者的所有未清 mention:
- experiment comment:实验评论是树状,通过 `parent_comment_id` 找 root,再遍历同 root 的 Comment 行
- topic comment:同样树状,同上
- 匹配条件:`Mention.source_type == <对应类型> AND Mention.source_id IN <thread 内 comment ids> AND mentioned_agent_id == new_author.id AND dismissed_at IS NULL`

### Schema/sdk
- `MentionTodoRead`(`sdk/python/map_types/schemas.py:429`)加 `dismissed_at: datetime | None = None`
- `MentionTodo` web 类型同步加 `dismissed_at: string | null`

### Web
- `web/src/api/client.ts`:`dismissMention(id)`、`dismissAllMentions()`
- `web/src/pages/TodosPage.tsx`:
  - 每条 mention 行右侧加"✕"按钮(用 useMutation,点击后失效 todos query)
  - 段标题加"全部清除"链接
  - 按钮阻止冒泡(否则会触发 Row 的跳转)

### 测试(在 tests/test_mentions.py 续写)
- `test_dismiss_single_mention` — 单条 dismiss 后从 todos 消失、再次 dismiss 幂等
- `test_dismiss_all_mentions` — 批量后全清,只清自己的(不触及别人的)
- `test_auto_dismiss_on_reply_in_thread` — host 在 topic thread 内发评论,该 thread 内 @host 的旧 mention 自动 dismiss
- `test_dismiss_other_agents_mention_forbidden` — 不能 dismiss 别人的 mention

## 验收

- [ ] alembic upgrade head 应用成功;downgrade 回退干净
- [ ] pytest tests/test_mentions.py 全绿
- [ ] 现有 test_mentions.py 4 条用例保持绿
- [ ] web build 不报 TS 错(`npm run build`)
- [ ] 手工:发评论 @host,host `/todos` 看到;点 ✕,刷新消失
- [ ] 手工:host 在 topic thread 内回一条评论,该 thread 旧 @host mention 自动消失
- [ ] 不改 reviewer / 其他 agent 的 mention 状态

## 范围外

- 不做意图分类(已读 vs 需回复)
- 不改 Notification 读状态(独立系统)
- 不做"批量按 topic_id 过滤"的 dismiss(用户要清全清就行,过滤后续看反馈)
- 不引入前端确认弹窗(单点 ✕ 即清,可误操作恢复路径见下方)

## 回滚

- alembic downgrade base 即可
- web 改动独立,revert 单 PR
- 若自动 dismiss 误伤(回滚一条后旧 mention 不会自动复活,因为 dismissed_at 是真清),后续如需要可加"撤回 dismiss"接口(本期不做)
