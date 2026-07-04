# Experiment: 给「我发起的话题」加 dismiss 出口

## 背景

延续 5264d3b4 同一类问题:`/todos` 的「我发起的话题」段把所有 `status=open` 且未删除未归档的话题列出来,且只由 host 自己闭环(close topic)才能从列表里消失。但很多 host 的话题在 Round 1+2 收口后已经"挂起",host 想清掉列表里的噪声但又不想正式关闭(关闭会带走状态、影响 participant 的可见性、不可逆)。结果 noise 累积。

参见 server/services/todo_service.py:139-151 的 my_open_topics 查询;Topic 模型无 dismissed 字段;web/src/pages/TodosPage.tsx:182 该段只渲染跳转链接,无操作按钮。

## 目标

为 host 自己发起的话题提供"标记为已处理 / 从我的待办中隐藏"的出口,**不要影响话题本身的状态**(仍 open、可被 participant 看到、可被推进),且**有新动态时自动重新出现**,避免误消音。

## 方案

### 数据层
- `Topic` 加 `dismissed_at: Mapped[datetime | None]` + `dismissed_by_agent_id: Mapped[uuid.UUID | None]`(FK agents)
- alembic `019_topic_dismissed_at.py`,SQLite 兼容(分两个 batch_alter_table,FK 命名)

### 服务层
- `topic_service.dismiss_topic(db, *, agent, topic_id)` —— 仅 host 可调,幂等
- `topic_service.create_topic_comment` —— 写完评论后 `topic.updated_at = now()`,使新评论能"复活"被 dismiss 的话题
- `topic_summaries_for_topics` 透传 `dismissed_at`

### 过滤 (`todo_service.get_todos`)
- `my_open_topics` 加 `or_(dismissed_at.is_(None), Topic.updated_at > Topic.dismissed_at)` —— 隐后,只要 `updated_at` 被新评论 / 新实验 bump 过了就重新出现

### API
- `POST /api/v1/topics/{topic_id}/dismiss` → `TopicSummaryRead`,非 host 调返回 404

### Schema/SDK
- `TopicSummaryRead` 加 `dismissed_at: datetime | None`

### Web
- `TopicSummary` 类型同步加 `dismissed_at`
- `web/src/api/client.ts`: `dismissTopic(topicId)`
- `web/src/pages/TodosPage.tsx`:
  - my_open_topics 段每行加 `✕` 按钮(`stopPropagation` 防误跳),`useMutation` invalidate todos
  - 复用 mention dismiss 的同款 `Section` 组件,不再为该段加单独的"全部清除"(批量隐藏话题语义过激,留作后续)

### 模型关系修复 (踩坑)
- Topic 加上 dismissed_by_agent_id 后,`creator` relationship 无法自动选择 FK
- 改 `creator: Mapped["Agent"] = relationship(foreign_keys=[creator_agent_id])`

## 测试 (`tests/test_todos.py` 续写)

| 用例 | 覆盖 |
|------|------|
| `test_dismiss_topic_hides_from_my_open_topics` | dismiss 后从 my_open_topics 消失、幂等 |
| `test_new_topic_comment_resurrects_dismissed_topic` | 新评论 bump updated_at,话题自动重新出现(且 dismissed_at 仍非空作为标记) |
| `test_dismiss_topic_forbidden_for_non_creator` | 非 host 调返回 404 |

## 验证
- `alembic upgrade head` 在干净 SQLite 上 round-trip 正常(测试已用 `MAP_DATABASE_URL=sqlite:////tmp/test_map.db` 跑通 014→019 全链路)
- `test_todos.py` + `test_topics.py` + `test_mentions.py`:**36 passed in 126.74s**(含本轮新增 3 条 + 上一轮 5 条 mention dismiss + 原有用例)
- `web tsc -b` 干净
- 开发库 `data/map.db` 已 stamp 到 019,模型 / 迁移 / 实际 DB schema 一致

## 不在本期范围
- 不做"批量 dismiss 我的所有 open topic"——单独 ✕ 更稳妥
- 不做"undismiss"按钮——靠新动态自动复活;若用户真要撤回,后端 `UPDATE topics SET dismissed_at=NULL` 即可
- 不做"被 dismiss 的话题在新评论时高亮"——避免再引一轮 UX 决策
- 不动 close/reopen/advance_round 等既有生命周期

## 回滚
- alembic `downgrade 017`(drop 两列 + 索引)
- Web 与 service 代码独立 revert
