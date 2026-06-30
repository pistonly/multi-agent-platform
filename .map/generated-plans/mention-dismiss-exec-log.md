# Execution Log — 实验 5264d3b4

## 落地清单

### 数据层
- `server/domain/models.py` — `Mention` 加 `dismissed_at: Mapped[datetime | None]`
- `alembic/versions/018_mention_dismissed_at.py` — 新迁移,加列 + 复合索引 `ix_mentions_mentioned_agent_id_dismissed_at`,`down_revision = "017"`

### Schema/SDK
- `sdk/python/map_types/schemas.py` — `MentionTodoRead` 加 `dismissed_at`;新增 `DismissMentionResultRead`、`DismissAllMentionsResultRead`

### 服务层 (`server/services/mention_service.py`)
- `list_mentions_for_agent` 默认 `where(dismissed_at.is_(None))`,加 `include_dismissed=False` 参数
- `dismiss_mention(db, *, agent, mention_id)` —— 校验归属,幂等
- `dismiss_all_for_agent(db, agent)` —— 批量 update,返回 rowcount
- `auto_dismiss_mentions_for_author_in_thread(...)` —— 同 thread 回复时自动清掉 thread 内对作者的 @ 提及
- 私有 helper `_experiment_thread_comment_ids` / `_topic_thread_comment_ids` —— 沿 `parent_comment_id` 找 thread root,Python 端 walk(避递归 SQL/避免 N 次 query)

### API (`server/api/agents.py`)
- `POST /api/v1/agents/me/mentions/{mention_id}/dismiss` → `DismissMentionResultRead`(归属校验失败 404)
- `POST /api/v1/agents/me/mentions/dismiss-all` → `DismissAllMentionsResultRead`

### 接入自动 dismiss
- `server/services/comment_service.create_comment` —— 实验评论写完后调用 `auto_dismiss_mentions_for_author_in_thread`
- `server/services/topic_service.create_topic_comment` —— topic 评论同上

### Web (`web/src/api/types.ts`, `web/src/api/client.ts`, `web/src/pages/TodosPage.tsx`)
- `MentionTodo` 类型加 `dismissed_at`
- `dismissMention(id)` / `dismissAllMentions()` 两个 client 方法
- `TodosPage`:
  - 每条 mention 行右侧加 `✕` 按钮(`useMutation` → invalidate todos query),`stopPropagation` 防止冒泡触发跳转
  - section header 右侧加 `全部清除` 按钮
  - `Section` 组件新增 `action?: ReactNode` 槽位(只用于 mentions 段,其它段不显示)

## 测试
`tests/test_mentions.py` 在原有 4 条基础上新增 5 条:

| 用例 | 覆盖点 |
|------|--------|
| `test_dismiss_single_mention` | 单条 dismiss + 幂等 + 从 todos 消失 |
| `test_dismiss_all_mentions` | 批量 dismiss + 只清自己的 |
| `test_dismiss_other_agents_mention_forbidden` | 不能 dismiss 别人的 mention(404) |
| `test_auto_dismiss_on_reply_in_topic_thread` | 同 thread 回复自动 dismiss |
| `test_auto_dismiss_does_not_touch_other_thread` | 自动 dismiss 仅限同 thread,不波及别的 |

结果:**9 passed in 36.26s**。

## 验证
- `alembic upgrade head` 应用成功;`downgrade -1` 回退干净;再次 `upgrade head` 正常
- 相关测试集 `test_mentions.py` + `test_notifications.py` + `test_topics.py` + `test_experiments.py` 35 passed in 114.93s
- `web tsc -b` 干净
- 与本改动**无关**的失败 (`tests/integration/test_experiment_lock*.py`, `tests/test_agents_list.py`) 均为测试隔离遗留(`Agent name already exists`),在 baseline 同样失败,未触及

## 不在本期范围
- 不做意图分类(已读 vs 需回复)——已在 plan 中讨论,不引入
- 不改 Notification 读状态
- 不做"撤回 dismiss"——已读就是已读,误点 ✕ 后续看反馈
- 不做按 topic 过滤的批量 dismiss——"全部清除"足够

## 回滚
- alembic: `alembic downgrade 017`(drop column + index)
- 代码 revert 单 PR 即可,Web 改动独立
